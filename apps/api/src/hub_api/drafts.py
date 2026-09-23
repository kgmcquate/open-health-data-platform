"""Private, unfinished dashboard specs — the builder's workbench.

This is the half of the dashboard story `hub_api.library` deliberately is not.
A library row is *published*: it has a unique kebab-case name, a rendered page,
votes, a topic, and a spot on a public page. A draft is the opposite of all of
that:

  - **It is text, not a spec.** The column holds the YAML the author typed,
    exactly as typed. A draft that does not parse is the normal state of a
    dashboard halfway through being written, and refusing to store one would
    make "save my work" mean "only once it already works". Nothing renders a
    draft from this table — the builder posts YAML to
    `/api/builder/preview`, which parses and validates it there — so an
    unparseable row is inert, never a page anyone is served.

  - **It is private, and keyed by its author.** Every read and write below
    takes `author_email` and filters on it, so a draft id belonging to someone
    else is indistinguishable from one that does not exist. There is no public
    route onto this table at all (`hub_api.builder`), and `title` is the only
    thing here a *published* dashboard would also carry.

  - **It is addressed by id, not by name.** Two people drafting the same chart
    would otherwise collide on a name neither has published, and an author
    renaming their chart mid-edit would silently start a second draft. The
    parsed `name`/`title` are stored alongside as display hints, refreshed on
    every save, and are empty when the YAML does not parse.

Publishing is what moves a draft into `hub_api.library`, under the validated
`DashboardSpec` rules every other dashboard obeys. The draft is kept after
publishing rather than consumed: the next edit is a new version of the same
chart, and the author should not have to re-derive the spec from the published
page to make it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy import (
    delete as delete_statement,
)
from sqlalchemy.engine import Engine

from hub_api import db
from ohdp_shared import get_logger

log = get_logger(__name__)

# How many drafts one author may keep. A ceiling on a table anyone signed in
# can write to, not a considered quota: nobody is iterating on twenty-five
# charts at once, and the alternative to a limit is an unbounded per-user
# table.
MAX_DRAFTS_PER_AUTHOR = 25

# The longest draft accepted, in characters. A `DashboardSpec` with a
# hand-written Vega-Lite spec is a few kilobytes; this is generous enough that
# no real chart hits it and small enough that the table cannot be used as
# storage.
MAX_YAML_CHARS = 40_000

dashboard_drafts = Table(
    "dashboard_drafts",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # The signed-in identity that owns this draft, from the session — never
    # from the request body. Indexed because every query here filters on it.
    Column("author_email", String(320), nullable=False, index=True),
    # `DashboardSpec.name`/`title` as last parsed, for the drafts list. Empty
    # when the YAML does not parse, which is why neither is unique and neither
    # is the key.
    Column("name", String(64), nullable=False, default=""),
    Column("title", String(160), nullable=False, default=""),
    # The author's YAML, verbatim — comments, formatting and all. Not a JSON
    # spec: this round-trips into the same editor it came out of.
    Column("spec_yaml", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, index=True),
)


class TooManyDrafts(RuntimeError):
    """This author is at `MAX_DRAFTS_PER_AUTHOR` and this save would add one."""


def _row(row: Any) -> dict[str, Any]:
    """One draft as the builder sees it. `author_email` is not included: the
    only caller is the author themselves, who knows who they are, and it keeps
    an address out of a JSON payload that has no need for it."""
    return {
        "id": int(row.id),
        "name": row.name or "",
        "title": row.title or "",
        "spec_yaml": str(row.spec_yaml),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_for(engine: Engine, author_email: str) -> list[dict[str, Any]]:
    """This author's drafts, most recently edited first, with their YAML.

    The YAML comes with the listing on purpose: a draft is a few kilobytes,
    there are at most `MAX_DRAFTS_PER_AUTHOR` of them, and the builder's whole
    job is to load one into an editor — a second round trip per draft would buy
    nothing.
    """
    statement = (
        select(dashboard_drafts)
        .where(dashboard_drafts.c.author_email == author_email)
        .order_by(dashboard_drafts.c.updated_at.desc(), dashboard_drafts.c.id.desc())
    )
    with engine.connect() as connection:
        return [_row(row) for row in connection.execute(statement)]


def get(engine: Engine, draft_id: int, author_email: str) -> dict[str, Any] | None:
    """One draft, or `None` — including when it exists but belongs to someone
    else. Author scoping lives in the `where` clause of every query in this
    module rather than in a check the caller could forget."""
    statement = select(dashboard_drafts).where(
        dashboard_drafts.c.id == draft_id,
        dashboard_drafts.c.author_email == author_email,
    )
    with engine.connect() as connection:
        row = connection.execute(statement).first()
    return _row(row) if row is not None else None


def create(
    engine: Engine, *, author_email: str, spec_yaml: str, name: str = "", title: str = ""
) -> dict[str, Any]:
    """Store a new draft for this author. Raises `TooManyDrafts` at the cap."""
    now = datetime.now(UTC)
    with engine.begin() as connection:
        total = int(
            connection.execute(
                select(func.count())
                .select_from(dashboard_drafts)
                .where(dashboard_drafts.c.author_email == author_email)
            ).scalar_one()
        )
        if total >= MAX_DRAFTS_PER_AUTHOR:
            raise TooManyDrafts(str(total))
        # `returning`, not `inserted_primary_key`: the id is the handle the
        # builder addresses this draft by from here on, and asking the database
        # for it in the same statement is one fewer thing to be `None`.
        draft_id = int(
            connection.execute(
                dashboard_drafts.insert()
                .values(
                    author_email=author_email,
                    name=name[:64],
                    title=title[:160],
                    spec_yaml=spec_yaml,
                    created_at=now,
                    updated_at=now,
                )
                .returning(dashboard_drafts.c.id)
            ).scalar_one()
        )
        row = connection.execute(
            select(dashboard_drafts).where(dashboard_drafts.c.id == draft_id)
        ).first()
    return _row(row)


def update(
    engine: Engine,
    draft_id: int,
    *,
    author_email: str,
    spec_yaml: str,
    name: str = "",
    title: str = "",
) -> dict[str, Any] | None:
    """Replace a draft's YAML. `None` when it is not this author's draft.

    Whole-text replacement rather than a patch: the editor holds the entire
    document and there is no version of "part of a YAML file changed" that this
    table would be better off knowing about.
    """
    now = datetime.now(UTC)
    with engine.begin() as connection:
        result = connection.execute(
            dashboard_drafts.update()
            .where(
                dashboard_drafts.c.id == draft_id,
                dashboard_drafts.c.author_email == author_email,
            )
            .values(spec_yaml=spec_yaml, name=name[:64], title=title[:160], updated_at=now)
        )
        if result.rowcount == 0:
            return None
        row = connection.execute(
            select(dashboard_drafts).where(dashboard_drafts.c.id == draft_id)
        ).first()
    return _row(row)


def delete(engine: Engine, draft_id: int, author_email: str) -> bool:
    """Throw a draft away. False when it is not this author's draft.

    Nothing else points at a draft — publishing copies the spec into
    `hub_api.library` rather than referencing it — so deleting one cannot
    affect a published dashboard.
    """
    with engine.begin() as connection:
        result = connection.execute(
            delete_statement(dashboard_drafts).where(
                dashboard_drafts.c.id == draft_id,
                dashboard_drafts.c.author_email == author_email,
            )
        )
    return bool(result.rowcount)
