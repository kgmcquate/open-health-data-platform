"""The dashboard library: one table behind the chat tools and the public page.

`dashboards` used to be `curated_dashboards` (`hub_api.content`), a table only
we wrote to, precisely so that a prompt-injected chat turn could not publish
onto a public page. That boundary is gone by decision, not by accident: a
dashboard the agent saves (`save_dashboard`, `hub_api.dashboards`) is the same
row the Dashboards page lists, and **votes are what sorts good from bad after
the fact**. Two consequences worth being honest about:

  - A newly saved dashboard is visible immediately, at score 0. The first
    person to see a bad one is a visitor, not a reviewer.
  - Voting needs a signed-in identity (one row per voter per dashboard, and a
    changed vote replaces it) because otherwise the ranking is writable by
    anyone who can send a POST. That makes votes a *quality* signal, not a
    security control: `HIDE_AT_SCORE` hides what people disliked, and nothing
    here stops a bad dashboard from being seen before it is voted on.

What keeps that survivable is what a dashboard actually is. A row holds a
`DashboardSpec` — a Cube query plus a Vega-Lite spec with no literal data at
any depth (`ohdp_agent.dashboard`) — and the page *we* rendered from it, never
markup anyone else wrote. `save_dashboard` runs the query and renders that page
before writing, so every number a visitor sees came out of the semantic layer
through the same validated `CubeQuery` as every other answer here. A saved
dashboard cannot carry an invented figure.

**Topics** are resolved from OpenMetadata at save time rather than authored:
the cubes a query names map to curated tables of the same name, and those
tables carry (or inherit) the Consumer-aligned domain that *is* a topic
(`ohdp_agent.domains`). So a dashboard joins the right topic page because of
what it queries, not because a model was asked to pick a label — and a topic
renamed in the catalog is a re-save away rather than a schema change.

**The stored render.** Alongside the spec, a row keeps the rendered HTML page
(`ohdp_agent.dashboard.render_html` — the same document the chat embeds) and
the `last_rendered` time it was produced. That is what the public page serves,
so opening the Dashboards page is a read from this table rather than one Cube
query per card. The cost is that a stored render is a *picture*: it holds the
rows as they were at `last_rendered`, so unlike the spec it can go stale. Two
things keep that honest — `last_rendered` and `stale` are on every listing, so
a reader always sees how old the numbers are, and viewing a stale dashboard schedules a
re-render (`hub_api.content`), so the next viewer gets fresh ones. The spec
stays the source of truth: a render is always reproducible from it, and is
replaced wholesale rather than edited.

**On `curated_dashboards`.** The old table is left where it is, orphaned and
unread. Nothing ever wrote to it, so there is nothing to migrate; `dashboards`
is created fresh by `create_all` and every column that matters is NOT NULL,
which is only possible because no legacy row has to be accommodated. Drop the
old table by hand whenever you like.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from hub_api import db
from ohdp_agent.dashboard import DashboardSpec
from ohdp_agent.models import CubeQuery
from ohdp_shared import get_logger

log = get_logger(__name__)

# Net score at or below which a dashboard drops off the public page. Two more
# downvotes than upvotes is a low bar on purpose: a dashboard nobody defends
# should stop being the first thing a visitor sees, and hiding is reversible —
# the row stays, the agent can still read and correct it, and one upvote brings
# it back.
HIDE_AT_SCORE = -2

# A ceiling on the library, not a quota — see `save`.
MAX_DASHBOARDS = 200

# How old a stored render may be before a view schedules a fresh one. Most of
# this warehouse lands new data daily, so an hour is well inside "today's
# numbers" while still collapsing a burst of visitors into one re-render.
STALE_AFTER_SECONDS = 3600

dashboards = Table(
    "dashboards",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # `DashboardSpec.name` — kebab-case, and the handle every tool and route
    # addresses a dashboard by. Unique because it is the key a re-save
    # overwrites on: saving "flu-ed-visits" twice is a correction, not a
    # second chart under a name already taken.
    Column("name", String(64), nullable=False, unique=True, index=True),
    Column("title", String(160), nullable=False),
    Column("description", String(800), nullable=False, default=""),
    Column("caption", String(400), nullable=True),
    # The whole `DashboardSpec` as `model_dump(mode="json")`, and the source of
    # truth for this row: `html` below is derived from it and can always be
    # rebuilt, never the other way round.
    Column("spec", JSON, nullable=False),
    # The rendered page (`render_html`), served as-is by the public route. A
    # few hundred KB each at the top end — the chart's own data table is in
    # there — which is why `MAX_DASHBOARDS` is a ceiling worth having.
    Column("html", Text, nullable=False),
    # When `html` was produced. This is the staleness clock: the spec cannot go
    # stale, the render can, and a reader is always told which they are looking
    # at.
    Column("last_rendered", DateTime(timezone=True), nullable=False),
    # "chat" (an agent save) or "curated" (written by us). Display only —
    # neither is trusted more than the other, since both are the same
    # validated spec.
    Column("source", String(16), nullable=False, default="curated"),
    # The signed-in email that saved it, where there was one. NEVER returned
    # by a public route: `public_rows` builds its own projection rather than
    # handing back the table's columns for exactly this reason.
    Column("saved_by", String(320), nullable=True),
    # Consumer-aligned OpenMetadata domain names, resolved from the query's
    # cubes at save time. A plain JSON list, filtered in Python like
    # `curated_literature.tags` — the table is a few hundred rows.
    Column("topics", JSON, nullable=False, default=list),
    # Ours, not a vote: a featured dashboard sorts above the ranking.
    Column("featured", Boolean, nullable=False, default=False),
    # Denormalized counts, recomputed from `dashboard_votes` inside the same
    # transaction as every vote — the votes table stays the source of truth,
    # and these exist so listing and ordering need no join.
    Column("upvotes", Integer, nullable=False, default=0),
    Column("downvotes", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, index=True),
)

dashboard_votes = Table(
    "dashboard_votes",
    db.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "dashboard_id",
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    # The verified session identity, never anything the client sent. This is
    # the whole spam guard: one row per person per dashboard, so a vote can be
    # changed or withdrawn but not repeated.
    Column("voter_email", String(320), nullable=False),
    # +1 or -1. A withdrawn vote deletes the row rather than storing 0, so
    # "has not voted" and "voted neutral" cannot drift apart.
    Column("value", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("dashboard_id", "voter_email", name="uq_dashboard_vote_voter"),
)


# --- reading ---------------------------------------------------------------


def _score(row: Any) -> int:
    return int(row.upvotes or 0) - int(row.downvotes or 0)


def is_stale(last_rendered: datetime | None) -> bool:
    """Whether a stored render is old enough to be worth replacing.

    `None` counts as stale: a row with no render time is one whose page we
    cannot vouch for, and re-rendering it is cheap and idempotent.
    """
    if last_rendered is None:
        return True
    # A row read back from SQLite has no tzinfo; treat it as the UTC it was
    # written as, rather than raising on the subtraction.
    if last_rendered.tzinfo is None:
        last_rendered = last_rendered.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last_rendered).total_seconds() > STALE_AFTER_SECONDS


def _summary(row: Any) -> dict[str, Any]:
    """The shape every route returns — **not** the table's columns.

    `saved_by` is deliberately absent: it is a signed-in user's email address
    and the Dashboards page is public and unauthenticated. `source` says where
    a dashboard came from without naming anyone. `html` is absent for a
    different reason: it is the biggest column in the table and a listing of
    twenty of them is megabytes — the public route serves one page at a time.
    """
    return {
        "id": int(row.id),
        "name": row.name,
        "title": row.title,
        "description": row.description or "",
        "caption": row.caption,
        "topics": list(row.topics or []),
        "featured": bool(row.featured),
        "source": row.source or "curated",
        "upvotes": int(row.upvotes or 0),
        "downvotes": int(row.downvotes or 0),
        "score": _score(row),
        "hidden": _score(row) <= HIDE_AT_SCORE,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # The age of the *numbers*, which is not the age of the dashboard: a
        # spec saved a month ago and re-rendered an hour ago shows both.
        "last_rendered": row.last_rendered.isoformat() if row.last_rendered else None,
        "stale": is_stale(row.last_rendered),
    }


def _my_votes(connection: Connection, ids: list[int], voter_email: str | None) -> dict[int, int]:
    if not voter_email or not ids:
        return {}
    statement = select(dashboard_votes.c.dashboard_id, dashboard_votes.c.value).where(
        dashboard_votes.c.voter_email == voter_email,
        dashboard_votes.c.dashboard_id.in_(ids),
    )
    return {int(row.dashboard_id): int(row.value) for row in connection.execute(statement)}


def public_rows(
    engine: Engine,
    *,
    topic: str | None = None,
    viewer_email: str | None = None,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """What the Dashboards page lists: visible dashboards, best first.

    Ordering is `featured`, then score, then recency — so our own picks stay on
    top, the crowd sorts the rest, and a tie goes to the newer dashboard rather
    than to whichever row the database happened to return first.

    The query is included; the `vega` spec is not. A list of specs is only
    useful once its rows are bound, which is what the per-dashboard data route
    does — see `hub_api.content`.
    """
    statement = select(dashboards).order_by(
        dashboards.c.featured.desc(),
        (dashboards.c.upvotes - dashboards.c.downvotes).desc(),
        dashboards.c.updated_at.desc(),
        dashboards.c.id.desc(),
    )
    with engine.connect() as connection:
        rows = list(connection.execute(statement))
        if topic is not None:
            rows = [row for row in rows if topic in (row.topics or [])]
        if not include_hidden:
            rows = [row for row in rows if _score(row) > HIDE_AT_SCORE]
        votes = _my_votes(connection, [int(row.id) for row in rows], viewer_email)

    listed = []
    for row in rows:
        entry = _summary(row)
        entry["query"] = dict(row.spec).get("query", {})
        entry["my_vote"] = votes.get(int(row.id), 0)
        listed.append(entry)
    return listed


def by_name(engine: Engine, name: str) -> dict[str, Any] | None:
    """One dashboard, with its full spec — the agent's read and the render
    path's read. Hidden dashboards are returned: hiding is a display rule for
    the public page, not a deletion, and the agent has to be able to see and
    correct what was voted down."""
    with engine.connect() as connection:
        row = connection.execute(select(dashboards).where(dashboards.c.name == name)).first()
    if row is None:
        return None
    entry = _summary(row)
    entry["spec"] = dict(row.spec)
    return entry


def rendered_page(engine: Engine, name: str) -> tuple[str, datetime | None] | None:
    """The stored HTML page and when it was rendered, or `None` if unknown.

    Separate from `by_name` because this is the one read that pulls the big
    column: everything else in this module deliberately leaves `html` behind.
    """
    statement = select(dashboards.c.html, dashboards.c.last_rendered).where(
        dashboards.c.name == name
    )
    with engine.connect() as connection:
        row = connection.execute(statement).first()
    return (str(row.html), row.last_rendered) if row is not None else None


def store_render(engine: Engine, *, name: str, html: str) -> None:
    """Replace a dashboard's rendered page with a fresher one.

    `updated_at` is deliberately untouched: the dashboard did not change, its
    numbers did. Moving it would reorder the library on every background
    re-render and would invalidate nothing that should be invalidated.
    """
    statement = (
        update(dashboards)
        .where(dashboards.c.name == name)
        .values(html=html, last_rendered=datetime.now(UTC))
    )
    with engine.begin() as connection:
        connection.execute(statement)


def names(engine: Engine, *, limit: int | None = None) -> list[str]:
    """Saved names, most recent first — for a "no such dashboard" message."""
    statement = select(dashboards.c.name).order_by(dashboards.c.updated_at.desc())
    if limit is not None:
        statement = statement.limit(limit)
    with engine.connect() as connection:
        return [str(value) for value in connection.execute(statement).scalars()]


def index(engine: Engine) -> list[dict[str, Any]]:
    """Every dashboard, specs omitted — what `get_dashboard` lists to the agent.
    Includes hidden ones, with their scores, for the same reason `by_name` does."""
    statement = select(dashboards).order_by(dashboards.c.updated_at.desc())
    with engine.connect() as connection:
        return [_summary(row) for row in connection.execute(statement)]


# --- writing ---------------------------------------------------------------


class LibraryFull(RuntimeError):
    """The library is at `MAX_DASHBOARDS` and this save would add another."""


def save(
    engine: Engine,
    spec: DashboardSpec,
    *,
    html: str,
    saved_by: str | None,
    topics: list[str],
    source: str = "chat",
) -> tuple[bool, int]:
    """Insert or overwrite the named dashboard. Returns (created, total).

    Not an upsert statement: SQLite and Postgres spell ON CONFLICT differently
    enough that a read-then-write in one transaction is both portable and
    easier to read. The race it leaves — two saves of the same new name at once
    — ends as a unique-constraint error on the loser, which is the right
    outcome anyway.

    An overwrite keeps the existing row's votes and `featured` flag. That is a
    real trade: a well-liked dashboard rewritten into a different chart carries
    its old score. It is the lesser of the two, since resetting votes on every
    correction would mean a typo fix costs a dashboard its standing — and the
    name is supposed to mean "this same chart" (see `save_dashboard`'s own
    guidance to the model).

    `html` is the page the caller has just rendered from this exact spec — a
    save cannot happen without one, because `save_dashboard` renders to
    validate anyway and throwing that render away only to redo it on the first
    view would be work for nothing.
    """
    now = datetime.now(UTC)
    # `exclude_defaults`, like `DashboardSpec.to_yaml`: what comes back out
    # should read as what someone chose, not as every field with its default
    # filled in. It still round-trips through `model_validate`.
    payload = spec.model_dump(mode="json", exclude_defaults=True)
    with engine.begin() as connection:
        existing = connection.execute(
            select(dashboards.c.id).where(dashboards.c.name == spec.name)
        ).first()
        if existing is None:
            total = int(
                connection.execute(select(func.count()).select_from(dashboards)).scalar_one()
            )
            if total >= MAX_DASHBOARDS:
                raise LibraryFull(str(total))
            connection.execute(
                dashboards.insert().values(
                    name=spec.name,
                    title=spec.title,
                    description=spec.description or "",
                    caption=spec.caption,
                    spec=payload,
                    html=html,
                    last_rendered=now,
                    source=source,
                    saved_by=saved_by,
                    topics=topics,
                    featured=False,
                    upvotes=0,
                    downvotes=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            connection.execute(
                dashboards.update()
                .where(dashboards.c.name == spec.name)
                .values(
                    title=spec.title,
                    description=spec.description or "",
                    caption=spec.caption,
                    spec=payload,
                    html=html,
                    last_rendered=now,
                    source=source,
                    saved_by=saved_by,
                    topics=topics,
                    updated_at=now,
                )
            )
        count = int(connection.execute(select(func.count()).select_from(dashboards)).scalar_one())
    return existing is None, count


def vote(engine: Engine, *, name: str, voter_email: str, value: int) -> dict[str, Any] | None:
    """Record one person's vote (+1/-1), or withdraw it with 0.

    The counters on `dashboards` are recomputed from `dashboard_votes` in the
    same transaction rather than incremented, so a changed vote (up, then down)
    cannot leave the two disagreeing — and a counter that somehow drifted is
    corrected by the next vote instead of staying wrong.
    """
    now = datetime.now(UTC)
    with engine.begin() as connection:
        row = connection.execute(select(dashboards.c.id).where(dashboards.c.name == name)).first()
        if row is None:
            return None
        dashboard_id = int(row.id)

        connection.execute(
            delete(dashboard_votes).where(
                dashboard_votes.c.dashboard_id == dashboard_id,
                dashboard_votes.c.voter_email == voter_email,
            )
        )
        if value:
            connection.execute(
                dashboard_votes.insert().values(
                    dashboard_id=dashboard_id,
                    voter_email=voter_email,
                    value=1 if value > 0 else -1,
                    created_at=now,
                )
            )

        counts = {
            int(tally.value): int(tally.total)
            for tally in connection.execute(
                select(dashboard_votes.c.value, func.count().label("total"))
                .where(dashboard_votes.c.dashboard_id == dashboard_id)
                .group_by(dashboard_votes.c.value)
            )
        }
        connection.execute(
            update(dashboards)
            .where(dashboards.c.id == dashboard_id)
            .values(upvotes=counts.get(1, 0), downvotes=counts.get(-1, 0))
        )
        updated = connection.execute(
            select(dashboards).where(dashboards.c.id == dashboard_id)
        ).first()

    assert updated is not None
    entry = _summary(updated)
    entry["my_vote"] = 1 if value > 0 else (-1 if value < 0 else 0)
    return entry


# --- topics ----------------------------------------------------------------


def cubes_in_query(query: CubeQuery) -> list[str]:
    """The cube names a query touches, in the order they first appear.

    Every member is `cube.field` (`ohdp_agent.models.Member` enforces the
    shape), so the cube is everything before the first dot. This is what the
    OpenMetadata topic lookup joins on: a cube is named for the dbt model it
    wraps, and that model is the curated table carrying the domain.
    """
    members = [
        *query.measures,
        *query.dimensions,
        *[td.dimension for td in query.time_dimensions],
        *[f.member for f in query.filters],
        *query.order,
    ]
    seen: list[str] = []
    for member in members:
        cube = member.split(".", 1)[0]
        if cube not in seen:
            seen.append(cube)
    return seen
