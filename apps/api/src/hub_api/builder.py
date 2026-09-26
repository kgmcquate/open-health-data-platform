"""The dashboard builder: a person writing a `DashboardSpec` by hand.

Until now a dashboard reached this platform one of two ways — we committed it,
or the chat agent saved one (ADR-0025, ADR-0028). This module is the third: a
signed-in visitor writes the YAML themselves in the browser, sees it drawn,
keeps it as a private draft while it is unfinished, and publishes it to the
same library everything else lands in.

**Nothing about what a dashboard *is* changes here.** Every route below parses
the author's YAML into the same `DashboardSpec` the agent's tools take, which
means the same validation: a `CubeQuery` the semantic layer has to accept, a
Vega-Lite spec with no literal `data` values at any depth, and rows bound by
the server from the query. So an author can draw anything Vega-Lite can draw
and cannot put a number on a page that did not come out of Cube — the property
`ohdp_agent.dashboard` exists to hold, applied to a human instead of a model.
It is also why there is no HTML anywhere in a request body: what a visitor
posts is a spec, and the page is one *we* render from it.

Three routes are worth reading as a set, because they are the three states a
dashboard has here:

  - `POST /api/builder/preview` — parse, run, render, return the page. Stores
    nothing. This is the "renders live" half, and it is why the editor needs no
    Vega or YAML library of its own: the same `render_html` that draws a
    published dashboard draws the preview, so what an author sees while editing
    is exactly what a visitor would get.
  - `/api/builder/drafts` — private work in progress, as text
    (`hub_api.drafts`). A draft need not parse, render or even be valid YAML.
  - `POST /api/builder/publish` — validate by drawing, then write to
    `hub_api.library` with `source="user"`. **This is publishing**: the
    dashboard appears on the public Dashboards page and on its topic's page
    immediately, at score 0, for anyone. The route says so in its summary and
    the UI confirms before calling it.

**Ownership.** A library name is one flat namespace, and saving over a name
replaces what was there. That was fine when only we and the agent saved; with
anyone able to publish it would let one author take over another's dashboard
along with the votes it earned. So an overwrite has to be the owner's, and the
owner is an *address* (`saved_by`) rather than a channel: a dashboard someone
published here and one the agent saved for them in a signed-in chat are both
theirs to correct, while ours and the agent's own are nobody's to take.
`library.save` holds that rule, and `publish` checks it up front — before
spending a Cube query — so the author gets "that name is taken" rather than a
failure after a render.

**The identity wall.** Every route here requires the hub's own session
(`chat.get_user_email`), including the read-only preview and the cube
reference. Preview runs a Cube query the caller composed, and while the data is
public and the query is validated, an anonymous route that does that is a query
runner for anyone who can send a POST. Drafts and the author's own published
list are scoped to the signed-in address in the query itself
(`hub_api.drafts`, `library.rows_saved_by`), never to an id or email in the
request.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Annotated, Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.engine import Engine

from hub_api import dashboards, drafts, library
from hub_api.chat import get_user_email
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.dashboard import NAME_RE, DashboardSpec
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# The semantic layer's shape (`/meta`) changes when the cubes are redeployed,
# not between requests, and every author opening the builder asks for it. Cached
# per process for this long rather than per request.
_CUBES_TTL_SECONDS = 300.0

# How long to wait for Cube's `/meta`. Short: the reference panel is help text,
# and an editor that hangs on it is worse than one that opens without it.
_META_TIMEOUT_SECONDS = 20.0

# `(fetched_at, payload)`, or None before the first fetch. Process-local, so
# each replica warms its own — the payload is a few kilobytes.
_cubes_cache: tuple[float, list[dict[str, Any]]] | None = None

router = APIRouter(prefix="/api", tags=["builder"])


def get_engine(request: Request) -> Engine:
    """The pool that holds drafts and the library.

    Declared after the identity dependency on every route below, for the reason
    `chat.get_engine` gives: a signed-out caller should not learn from a 503
    whether our database is up.
    """
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The dashboard database is not available.")
    return engine


class SpecText(BaseModel):
    """A spec as the author typed it. YAML, not JSON, because that is the form
    a dashboard is written and committed in (`DashboardSpec.to_yaml`) and the
    form the rendered card shows its own source in — so a reader can copy a
    published chart's source straight into the editor. JSON still works: it is
    a subset of YAML."""

    spec_yaml: str = Field(min_length=1, max_length=drafts.MAX_YAML_CHARS)


class DraftText(SpecText):
    """A draft body. Same field, and deliberately no `name`/`title`: those are
    read out of the YAML when it parses, so the two cannot disagree."""


class Draft(BaseModel):
    id: int
    # Empty when the YAML does not (yet) parse — a draft is allowed to be
    # mid-sentence, see `hub_api.drafts`.
    name: str = ""
    title: str = ""
    spec_yaml: str
    created_at: str | None = None
    updated_at: str | None = None


class Preview(BaseModel):
    """A drawn page, for a sandboxed iframe — never for insertion into the hub's
    own document. The row counts come along because the chart cannot show that
    it was truncated, and an author deciding whether to publish should know."""

    name: str
    title: str
    html: str
    row_count: int
    truncated: bool


class PublishResult(BaseModel):
    name: str
    title: str
    # False when this replaced the author's own dashboard of the same name.
    created: bool
    # The topics the catalog filed it under; empty when no cube in the query
    # mapped to a catalogued table, or when OpenMetadata was unreachable.
    topics: list[str]
    # Where it is now public.
    url: str


class MeasureRef(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    agg_type: str = ""


class DimensionRef(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    type: str = ""


class CubeRef(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    measures: list[MeasureRef] = Field(default_factory=list)
    dimensions: list[DimensionRef] = Field(default_factory=list)


def _spec_from_yaml(text: str) -> DashboardSpec:
    """Parse and validate an author's YAML, or 422 with something they can act on.

    The two failures are told apart deliberately. Malformed YAML is a typing
    mistake and the parser already says where it is; a valid document that is
    not a `DashboardSpec` is a *modelling* mistake, and pydantic's field paths
    (`vega_lite.encoding`, `query.measures.0`) are the only thing that says which
    key is wrong. Both come back as text in the editor's error strip, so both
    are flattened to one readable line per problem rather than returned as
    pydantic's nested JSON.
    """
    try:
        # `safe_load`, for the same reason `DashboardSpec.from_yaml` uses it —
        # and more so here, where the document arrives from a browser rather
        # than from a reviewed file in the repo.
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"That is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(422, "A dashboard spec must be a YAML mapping of fields.")
    try:
        return DashboardSpec.model_validate(parsed)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'spec'}: {error['msg']}"
            for error in exc.errors()[:8]
        )
        raise HTTPException(422, f"That spec is not valid: {problems}") from exc


def _display_fields(text: str) -> tuple[str, str]:
    """`(name, title)` for a draft's listing, best-effort.

    Deliberately forgiving where `_spec_from_yaml` is strict: this runs on every
    draft save, including one that does not parse, and its only job is to label
    a row in a list. Anything it cannot read is an empty string, never an error.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return "", ""
    if not isinstance(parsed, dict):
        return "", ""
    name = parsed.get("name")
    title = parsed.get("title")
    return (
        str(name)[:64] if isinstance(name, str) else "",
        str(title)[:160] if isinstance(title, str) else "",
    )


@router.post("/builder/preview")
async def preview(
    body: SpecText,
    author_email: Annotated[str, Depends(get_user_email)],
) -> Preview:
    """Draw a spec without saving anything — the editor's live render.

    Nothing is written, and the author's identity is not recorded: this is a
    read of the semantic layer shaped by a query we validated. The page comes
    back as a string for the caller to put in a sandboxed iframe's `srcdoc`
    (`DashboardEmbed`), which is also how the chat embeds a rendered tool
    result.
    """
    spec = _spec_from_yaml(body.spec_yaml)
    html, data = await dashboards.render_from_spec(spec)
    log.info("dashboard_previewed", name=spec.name, rows=data.row_count)
    return Preview(
        name=spec.name,
        title=spec.title,
        html=html,
        row_count=data.row_count,
        truncated=data.truncated,
    )


@router.get("/builder/drafts")
def list_drafts(
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[Draft]:
    """This author's drafts, newest edit first, with their YAML."""
    return [Draft.model_validate(row) for row in drafts.list_for(engine, author_email)]


@router.post("/builder/drafts")
def create_draft(
    body: DraftText,
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> Draft:
    """Keep a new draft. The YAML does not have to parse."""
    name, title = _display_fields(body.spec_yaml)
    try:
        row = drafts.create(
            engine, author_email=author_email, spec_yaml=body.spec_yaml, name=name, title=title
        )
    except drafts.TooManyDrafts as exc:
        raise HTTPException(
            409,
            f"You already have {exc} drafts, which is the limit. Delete one you "
            "have finished with, or publish it.",
        ) from exc
    log.info("dashboard_draft_created", draft_id=row["id"])
    return Draft.model_validate(row)


@router.put("/builder/drafts/{draft_id}")
def update_draft(
    body: DraftText,
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
    draft_id: Annotated[int, Path(ge=1)],
) -> Draft:
    """Replace a draft's YAML. 404 for a draft that is not this author's —
    someone else's id and a nonexistent one are the same answer on purpose."""
    name, title = _display_fields(body.spec_yaml)
    row = drafts.update(
        engine,
        draft_id,
        author_email=author_email,
        spec_yaml=body.spec_yaml,
        name=name,
        title=title,
    )
    if row is None:
        raise HTTPException(404, "No such draft.")
    return Draft.model_validate(row)


@router.delete("/builder/drafts/{draft_id}")
def delete_draft(
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
    draft_id: Annotated[int, Path(ge=1)],
) -> dict[str, bool]:
    """Throw a draft away. Published dashboards are untouched: publishing
    copies the spec into the library rather than pointing at the draft."""
    if not drafts.delete(engine, draft_id, author_email):
        raise HTTPException(404, "No such draft.")
    log.info("dashboard_draft_deleted", draft_id=draft_id)
    return {"ok": True}


@router.post("/builder/publish", summary="Publish a dashboard to the public library")
async def publish(
    body: SpecText,
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> PublishResult:
    """Validate by drawing, then publish to the shared library under `name`.

    The order matters and is the same one `save_dashboard` uses: the query runs
    and the page renders *before* anything is written, so a published dashboard
    is one that draws, and the render is kept as the page visitors are served
    rather than thrown away and redone on the first view.

    Publishing the same `name` again replaces the author's own dashboard,
    keeping its votes — that is how a chart is corrected, and "their own" is by
    address, so a dashboard the agent saved for them in a signed-in chat counts.
    A name someone else published, or one of ours or the agent's own, is refused
    with a 409 instead of quietly taken over.
    """
    spec = _spec_from_yaml(body.spec_yaml)

    # Checked before the Cube query, not after: the answer does not depend on
    # the render, and an author who picked a taken name should not wait for a
    # chart to be drawn to hear it. `library.save` enforces the same rule at
    # write time, which is what actually closes the race between two publishes.
    owner = await asyncio.to_thread(library.owner_of, engine, spec.name)
    if owner is not False and owner != author_email:
        raise HTTPException(
            409,
            f"The name {spec.name!r} already belongs to a published dashboard "
            "that is not yours. Pick a different name.",
        )

    html, data = await dashboards.render_from_spec(spec)
    topics = await dashboards.file_in_catalog(spec)

    try:
        created, count = await asyncio.to_thread(
            library.save,
            engine,
            spec,
            html=html,
            saved_by=author_email,
            topics=topics,
            source="user",
        )
    except library.LibraryFull as exc:
        raise HTTPException(
            409,
            f"The dashboard library is full ({exc} published). Nothing more can be "
            "published until an admin removes one.",
        ) from exc
    except library.LibraryOwned as exc:
        # The up-front check above lost a race with another publish of the same
        # new name. Same answer, one step later.
        raise HTTPException(
            409,
            f"The name {exc!s} was just published by someone else. Pick a different name.",
        ) from exc

    log.info(
        "dashboard_published_by_user",
        name=spec.name,
        created=created,
        topics=topics,
        rows=data.row_count,
        count=count,
    )
    return PublishResult(
        name=spec.name,
        title=spec.title,
        created=created,
        topics=topics,
        url=f"/dashboards/{spec.name}",
    )


@router.get("/builder/published")
def list_published(
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[dict[str, Any]]:
    """The dashboards this author has published, newest edit first.

    Shaped like a `/api/dashboards` entry so the same card renders it, and
    including ones voted below `library.HIDE_AT_SCORE`: a hidden dashboard has
    dropped off the public page, and its author is the one person who can
    republish it fixed.

    Plus `spec_yaml`, which a public entry never carries: the source of the
    author's own chart, in the form the editor takes, so correcting a published
    dashboard is loading it back and publishing again rather than rewriting the
    Vega-Lite from the picture. Rendered from the stored spec by `to_yaml`
    rather than kept as text — the library stores validated specs, and this is
    the same YAML `save_dashboard`'s card shows.
    """
    entries = library.rows_saved_by(engine, author_email)
    for entry in entries:
        stored = entry.pop("spec", None)
        entry["spec_yaml"] = _yaml_of(stored)
    return entries


@router.delete("/builder/published/{name}")
async def delete_published(
    author_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
    name: Annotated[str, Path(pattern=NAME_RE.pattern, max_length=64)],
) -> dict[str, bool]:
    """Take down a dashboard this author published — row, stored render and
    votes, for good (`library.delete`).

    The author's counterpart to the admin delete in `hub_api.content`, scoped
    by address the same way republishing is: a dashboard is theirs to remove
    exactly when it is theirs to overwrite. 404 for a name that is not theirs —
    someone else's, ours, the agent's and a nonexistent one are the same answer
    on purpose, as with drafts.

    The OpenMetadata mirror that publishing created (`dashboards.
    file_in_catalog`) goes too, after the row and best-effort: a catalog
    outage leaves a stale entry behind rather than failing a delete that has
    already happened.
    """
    entry = await asyncio.to_thread(library.delete, engine, name, owned_by=author_email)
    if entry is None:
        raise HTTPException(404, "No such dashboard of yours.")
    await dashboards.unfile_from_catalog(name)
    log.info(
        "dashboard_deleted_by_author",
        name=name,
        title=entry["title"],
        score=entry["score"],
    )
    return {"ok": True}


def _yaml_of(stored: Any) -> str | None:
    """A stored spec as editable YAML, or `None` if it cannot be read back.

    `None` rather than an error: a spec this module cannot revalidate is a
    dashboard whose *listing* should still load — the entry keeps its title,
    score and link, and only the "load this back into the editor" button is
    missing. The only way to get one is a `DashboardSpec` that has since grown a
    stricter field.
    """
    if not isinstance(stored, dict):
        return None
    try:
        return DashboardSpec.model_validate(stored).to_yaml()
    except ValidationError:
        log.warning("dashboard_spec_not_editable")
        return None


@router.get("/semantic/cubes")
async def list_cubes(
    author_email: Annotated[str, Depends(get_user_email)],
) -> list[CubeRef]:
    """Every cube, measure and dimension a spec's query may name.

    The builder's field reference, and the same `/meta` the agent's
    `list_metrics` tool reads — so what an author can write is exactly what the
    agent can ask for (ARCHITECTURE.md §1.6). Column names in a `vega_lite` spec are
    these member names as Cube returns them, which is why the panel exists: a
    mistyped member is the single most common way a hand-written spec comes
    back empty.

    Signed-in like the rest of the builder, and cached per process for
    `_CUBES_TTL_SECONDS`: the shape changes when the cubes are redeployed, not
    per request.
    """
    global _cubes_cache

    now = time.monotonic()
    if _cubes_cache is not None and now - _cubes_cache[0] < _CUBES_TTL_SECONDS:
        return [CubeRef.model_validate(entry) for entry in _cubes_cache[1]]

    client = CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=dashboards.TIER)
    try:
        async with asyncio.timeout(_META_TIMEOUT_SECONDS):
            cubes = await client.list_metrics()
    except CubeError as exc:
        raise HTTPException(502, f"The semantic layer did not answer: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(504, "The semantic layer did not answer in time.") from exc
    except httpx.HTTPError as exc:
        # Unreachable rather than unhappy. The panel is help text, so this is a
        # 502 the editor shows as "field list unavailable" and nothing more.
        log.warning("cube_meta_unreachable", error=str(exc))
        raise HTTPException(502, "The semantic layer could not be reached.") from exc

    payload = [asdict(cube) for cube in cubes]
    _cubes_cache = (now, payload)
    return [CubeRef.model_validate(entry) for entry in payload]
