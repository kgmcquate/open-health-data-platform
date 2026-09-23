"""Draw a dashboard into the chat turn, and keep the ones worth keeping (ADR-0025).

Two halves, one spec. `render_dashboard` draws a `DashboardSpec` for this turn
and stores nothing; `save_dashboard`/`get_dashboard` are the library — the same
spec, kept in the `saved_dashboards` table under its `name`, so the next person
to ask gets the chart back instead of the model rewriting it. What
`get_dashboard` returns is a `render_dashboard` payload exactly as it was
saved, which is the whole contract between the two halves: there is no second
shape to keep in step, and a spec that draws today draws when it comes back
out.

What the library deliberately does not hold is rows. A saved dashboard is a
question, not an answer (`ohdp_agent.dashboard`'s own docstring), so it re-runs
against the semantic layer every time it is drawn and cannot go stale or
disagree with it. Saving is validated by doing — the query runs and the chart
renders before anything is written — so the failure a stored spec would
otherwise have (drawing fine when it was written, failing when it is opened) is
paid for at save time, where the model can still fix it.

The library is shared, flat and **public**: one namespace, and a saved
dashboard is the same row the Dashboards page lists and readers vote on
(`hub_api.library` holds the table and says what that costs). Saving is
therefore publishing, which is why `save_dashboard`'s description tells the
model to ask first when the user did not.

The rest of this docstring is about the rendering half.

The hub chat renders a tool result as an interactive iframe when the tool
answers with `Content-Type: text/html` **and** `Content-Disposition: inline`.
That is why these operations live here, on hub-api's `/tools` app: the
`ohdp-tools` connection wraps this OpenAPI spec as an in-process MCP server
(`hub_api.tool_connections`), and that wrapper strips HTTP response headers,
so the only signal left on the agent side that the result is a page to render
is the HTML body itself. An MCP tool has no way to ask for an embed.

The model writes its Vega-Lite, but never the data. It sends a
`DashboardSpec` — a title, one Cube query, and a `vega` spec — and this
module runs the query and binds the rows (`ohdp_agent.dashboard`). Literal
`data` values anywhere in a `vega` spec are rejected before they can reach a
browser; a `data` block survives only as a remote `url` reference (e.g. a
choropleth's basemap geometry).
Two consequences worth stating plainly:

  - **The numbers cannot be invented.** Rows reach the page from Cube, through
    the same validated `CubeQuery` every other execution tool uses.
  - **The output is deterministic.** The same spec renders the same page, so a
    dashboard is reviewable as code rather than as a screenshot.

One limitation to know before extending this. The `ohdp-tools` wrapper strips
response headers, so the model only gets the HTTP body back and cannot see
status codes or error detail in the rendered page. So the model cannot read the
rows back out of a render, and the prompt tells it to run `run_metric_query`
first when it intends to say anything *about* the data. Rendering twice is
cheap — every cube is pre-aggregated (ADR-0024).

The same limitation means a rendered-but-broken chart cannot be reported inside
a 200 embed either: the agent would see a 200 HTML body and think the render
succeeded even when `render_html` could not bind the spec to the query's
columns. `_embed` turns that case into an HTTPException instead (see
`_run_query`, which already does this for a Cube-rejected query), so the model
gets the real reason back and can retry with a corrected spec.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.engine import Engine

from hub_api import library
from hub_api.issues import Reporter, get_reporter
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.dashboard import NAME_RE, ChartData, DashboardSpec, render_html
from ohdp_agent.dashboard_catalog import CatalogWriteError, DashboardCatalogClient
from ohdp_agent.domains import DomainsClient, DomainsError
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# Everyone reaching the chat is tier `free` (ARCHITECTURE.md §5), and tier is
# what Cube's `queryRewrite` reads to apply its row ceiling — so it is set here
# and never taken from the caller.
TIER = "free"

# Rendering an embed means holding the chart's rows in memory and returning a
# single HTML response.
_QUERY_TIMEOUT_SECONDS = 90.0

# The header pair that turns a tool result into an iframe instead of a wall of
# markup in the transcript. Both are required; `Content-Type` comes from
# HTMLResponse itself.
_EMBED_HEADERS = {"Content-Disposition": "inline"}

# How many names a "no such dashboard" error lists back to the model, so it can
# correct a near-miss without a second call.
_NAMES_IN_ERROR = 40

# Resolving a dashboard's topics is a handful of cached OpenMetadata reads
# (`ohdp_agent.domains`). A save must not hang on a slow catalog — topics are
# an enrichment, and a dashboard with none still saves and still renders.
_TOPICS_TIMEOUT_SECONDS = 20.0

# Publishing to the catalog is a service upsert, a dashboard upsert, and one
# table lookup + lineage edge per cube — more round trips than resolving
# topics, so it gets its own, slightly longer, cap. Same reasoning as above:
# an enrichment, never allowed to hang or fail the save.
_CATALOG_TIMEOUT_SECONDS = 30.0

dashboards_router = APIRouter()


def get_library_engine(request: Request) -> Engine:
    """The library's pool, read off the app handling the request.

    That app is `tools_app`, not the parent: a mounted sub-app resets
    `scope["app"]` to itself, so `hub_api.chat.get_engine`'s `request.app.state`
    lookup would find an empty state here. `hub_api.main`'s lifespan sets
    `tools_app.state.engine` alongside `app.state.engine` for this reason.

    503 rather than a silent empty library: "there are no saved dashboards" and
    "the database is down" must not look the same to the model, or it will
    cheerfully tell a user their dashboard is gone.
    """
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The dashboard library is not available.")
    return engine


class LibraryEntry(BaseModel):
    """One dashboard as the model sees it.

    `spec` is filled only when a single dashboard was asked for by name — an
    index carrying every spec would be several hundred kilobytes of Vega-Lite
    in a tool result no one reads. `score`/`hidden` are here because the model
    is the one that can act on them: a dashboard people voted down is one to
    fix or replace, and it should not be offered to the next person as though
    it were well liked.
    """

    name: str
    title: str
    description: str = ""
    caption: str | None = None
    topics: list[str] = Field(default_factory=list)
    source: str = "curated"
    upvotes: int = 0
    downvotes: int = 0
    score: int = 0
    hidden: bool = False
    updated_at: str | None = None
    spec: dict[str, Any] | None = None


class DashboardLibrary(BaseModel):
    count: int
    dashboards: list[LibraryEntry]


class SaveResult(BaseModel):
    name: str
    title: str
    # False when this replaced a dashboard already saved under the same name.
    created: bool
    count: int
    # The topics this dashboard was filed under, resolved from the catalog —
    # empty when no cube in the query mapped to a catalogued table, or when
    # OpenMetadata was unreachable.
    topics: list[str]


async def _topics_for(spec: DashboardSpec) -> list[str]:
    """The Consumer-aligned domains this dashboard's cubes belong to.

    Best-effort by design, exactly like `hub_api.content`'s public routes: an
    unconfigured or unreachable catalog means no topics, not a failed save.
    A dashboard with no topics still appears on the Dashboards page; it just
    does not appear on a topic page until someone saves it again with the
    catalog up.
    """
    if not settings.openmetadata_jwt:
        return []
    client = DomainsClient(
        settings.openmetadata_url,
        settings.openmetadata_jwt,
        link_base_url=settings.openmetadata_public_url,
    )
    cubes = library.cubes_in_query(spec.query)
    try:
        async with asyncio.timeout(_TOPICS_TIMEOUT_SECONDS):
            return await client.topics_for_tables(cubes)
    except (DomainsError, TimeoutError) as exc:
        log.warning("dashboard_topics_unresolved", name=spec.name, error=str(exc))
        return []


async def _publish_to_catalog(spec: DashboardSpec, topics: list[str]) -> None:
    """File this dashboard into OpenMetadata as a Dashboard entity, with
    lineage from the curated tables its query touches (`ohdp_agent.
    dashboard_catalog`).

    Best-effort, exactly like `_topics_for`: an unconfigured or unreachable
    catalog means the dashboard is not published there, not a failed save.
    The library row is the source of truth either way — this is purely an
    OpenMetadata-side mirror of it.
    """
    if not settings.openmetadata_jwt:
        return
    client = DashboardCatalogClient(settings.openmetadata_url, settings.openmetadata_jwt)
    url = f"{settings.hub_base_url.rstrip('/')}/dashboards/{spec.name}"
    try:
        async with asyncio.timeout(_CATALOG_TIMEOUT_SECONDS):
            await client.publish_dashboard(
                name=spec.name,
                title=spec.title,
                description=spec.description or "",
                url=url,
                table_names=library.cubes_in_query(spec.query),
                domains=topics,
            )
    except (CatalogWriteError, TimeoutError) as exc:
        log.warning("dashboard_catalog_unpublished", name=spec.name, error=str(exc))


def _client() -> CubeClient:
    return CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=TIER)


async def _run_query(spec: DashboardSpec) -> ChartData:
    """Fetch the chart's rows, or fail the render with Cube's reason.

    Cube's own message names the wrong member or the unsupported operator, and
    that message is usually enough for the model to correct its spec on the next
    attempt — so it is surfaced rather than replaced with a generic failure.
    """
    client = _client()

    try:
        async with asyncio.timeout(_QUERY_TIMEOUT_SECONDS):
            result = await client.run_metric_query(spec.query)
    except CubeError as exc:
        raise HTTPException(400, f"The semantic layer rejected the query: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(
            504,
            "The semantic layer did not answer in time. Try a shorter date "
            "range or a coarser granularity.",
        ) from exc

    return ChartData(
        rows=result["rows"],
        row_count=result["row_count"],
        truncated=bool(result["truncated"]),
    )


def _embed(spec: DashboardSpec, data: ChartData) -> HTMLResponse:
    """Render the embed, or fail the render with the reason.

    `render_html` raises `ValueError` when the `vega` spec names a column the
    query did not return. That must not become a 200 embed: the `ohdp-tools`
    wrapper only passes the HTML body back to the agent, so a spec/column
    mismatch would otherwise look like success to the model and it would never
    retry with a corrected spec. Raising instead puts the same reason `_run_query`
    already surfaces for a rejected Cube query onto this path too.
    """
    return HTMLResponse(content=_render(spec, data), headers=_EMBED_HEADERS)


def _render(spec: DashboardSpec, data: ChartData) -> str:
    """The render, with `render_html`'s reason turned into the tool's reason.

    Split out from `_embed` because `save_dashboard` needs the check without
    the page: a spec that cannot be drawn must not enter the library, and the
    model needs the same correctable message it gets from a failed render.
    """
    try:
        return render_html(spec, data)
    except ValueError as exc:
        raise HTTPException(422, f"This chart could not be drawn: {exc}") from exc


@dashboards_router.post(
    "/render_dashboard",
    operation_id="render_dashboard",
    summary="Draw a dashboard in the chat",
    # No `response_class=HTMLResponse` here: the handler always returns an
    # `HTMLResponse` instance directly, which FastAPI serves as-is regardless
    # of the route's declared `response_class` — that declaration only feeds
    # OpenAPI generation. Declaring it made FastAPI record a `{"type":
    # "string"}` schema for the 200 response (`fastapi.openapi.utils.
    # get_openapi_path` fills one in for any non-`JSONResponse` response
    # class, before the `responses=` override below is even merged in).
    # FastMCP's OpenAPI-to-MCP conversion then read that as a real output
    # schema and advertised it on `render_dashboard` in `list_tools()` — but
    # this tool's HTTP response is never JSON, so `OpenAPITool.run()` can
    # never populate `structured_content` for it, and the MCP client raises
    # "Tool render_dashboard has an output schema but did not return
    # structured content" on every call. Leaving `response_class` at
    # FastAPI's default (`JSONResponse`) makes it record an empty `{}`
    # schema instead, which FastMCP correctly treats as no output schema.
    responses={200: {"content": {"text/html": {}}, "description": "The rendered dashboard"}},
)
async def render_dashboard_tool(
    spec: DashboardSpec,
    # The identity check on hub-api's /tools app: a browser's session cookie, or
    # the shared bearer token from the configured `ohdp-tools` connection. The
    # caller's identity is not used here — rendering is read-only and the data is
    # public — but an unauthenticated route on this mount would be a Cube query
    # anyone in the cluster could run.
    _caller: Annotated[Reporter, Depends(get_reporter)],
) -> HTMLResponse:
    """Draw a chart of semantic-layer data directly in this chat.

    Use this whenever a question is better answered by a picture than by a
    table, and after `run_metric_query` has shown you the actual numbers — this
    tool runs its own query and does not report the rows back to you, so plan
    and describe the data first, then draw it.

    The spec carries a `query` in exactly the form `run_metric_query` takes,
    plus a `vega` Vega-Lite spec describing how to draw that query's rows. The
    spec may use anything Vega-Lite supports — `mark`, `encoding`, `transform`,
    `layer`, `params` — but must not carry literal `data` values: rows are
    bound from `query` by the server. The exceptions are a `data` block that
    is only a remote reference, `{"url": "...", "format": ...}`, which a
    choropleth may use for its basemap geometry, and an empty
    `{"values": []}`, which keeps the spec valid, parseable Vega-Lite at the
    exact spot — e.g. a `lookup` transform's `from.data` — where `data_path`
    will bind the real rows in. The query's rows are bound
    into the spec at `data_path` — a JSONPath, `$.data.values` by default; for
    a choropleth, where the top-level `data` is the geometry URL, set
    `data_path` to the lookup source instead, e.g.
    `$.transform[0].from.data.values`. Use column names exactly as they came
    back
    in the query result. Give every axis a title that carries its units, and
    title the chart with the filter scope it was run at.

    Aggregate to a top-N plus an "Other" bucket rather than plotting dozens of
    categories. The rendered card shows its own data table, the compiled query,
    and the chart's YAML source, so you do not need to repeat any of those in
    your reply. Tell the user the source is in the card if they want to reuse
    the spec.
    """
    data = await _run_query(spec)
    log.info("dashboard_rendered", name=spec.name)
    return _embed(spec, data)


async def refresh_render(engine: Engine, name: str, spec_payload: dict[str, Any]) -> None:
    """Re-run a saved dashboard's query and replace its stored page.

    Scheduled by the public route when what it just served was stale
    (`hub_api.content`), so the reader who found it stale is not the one who
    waits for Cube. It therefore runs with nobody listening: every failure is
    logged and swallowed, because the alternative to a fresh render is the
    perfectly serviceable older one, and an exception raised into a background
    task reaches no user at all.
    """
    try:
        spec = DashboardSpec.model_validate(spec_payload)
        data = await _run_query(spec)
        html = render_html(spec, data)
    except (HTTPException, ValidationError, ValueError) as exc:
        log.warning("dashboard_not_refreshed", name=name, error=str(exc))
        return

    await asyncio.to_thread(library.store_render, engine, name=name, html=html)
    log.info("dashboard_refreshed", name=name, rows=data.row_count)


@dashboards_router.post(
    "/save_dashboard",
    operation_id="save_dashboard",
    summary="Keep a dashboard in the library",
)
async def save_dashboard_tool(
    spec: DashboardSpec,
    _caller: Annotated[Reporter, Depends(get_reporter)],
    engine: Annotated[Engine, Depends(get_library_engine)],
) -> SaveResult:
    """Publish a chart to the dashboard library, under a reusable name.

    A saved dashboard is **public**: it appears on this platform's Dashboards
    page and on the page for whichever topic its data belongs to, for anyone
    who visits, and readers vote it up or down there. So save a chart once it
    is genuinely worth showing to someone who was not in this conversation —
    the user asked for it to be kept, or you built something people will
    plausibly ask for again. Do not save a one-off exploratory chart, a chart
    scoped to one person's private question, or one you have not already drawn
    with `render_dashboard` and shown to the user. When the user has not asked
    for it, ask before saving.

    `spec` is exactly what `render_dashboard` takes. The `name` is the library
    key: kebab-case, specific enough to recognise later
    (`flu-ed-visits-by-week`, not `chart-1`). Saving under a name that already
    exists **overwrites** it — do that to correct or improve a dashboard, and
    pick a different name for a genuinely different chart. Say which of the two
    happened when you tell the user it was saved; the result's `created` field
    reports it.

    Write `description` for a reader browsing the library — what question the
    chart answers and at what grain — and `caption` for the part the picture
    cannot show, such as a population restriction living in the filters.

    The spec's query is run and the chart is drawn before anything is stored,
    so a save that succeeds is a dashboard that still draws; a save that fails
    comes back with the same reason a failed render does, and the fix is the
    same. Rows are never stored — the query is re-run every time the dashboard
    is drawn, so a saved dashboard cannot go stale.

    Topics are not yours to pick: the catalog is asked which subject areas the
    query's cubes belong to, and the result comes back in `topics`. An empty
    `topics` means no cube matched a catalogued table — the dashboard is still
    published, it just does not appear on a topic page. Tell the user which
    topics it landed under if they asked where it would show up.
    """
    # Validate by doing: a spec enters the library only if it queries and draws
    # right now. This is one extra Cube query per save, against pre-aggregated
    # cubes (ADR-0024), and it is what stops the library filling with charts
    # that fail the first time someone opens them.
    data = await _run_query(spec)
    # The render is kept, not discarded: it is the page the public Dashboards
    # route serves, so validating the spec and producing what visitors see are
    # the same piece of work (`hub_api.library`'s docstring on stored renders).
    html = _render(spec, data)

    topics = await _topics_for(spec)
    await _publish_to_catalog(spec, topics)

    # `engine` is synchronous SQLAlchemy inside an async route — off the loop,
    # since this handler is already `async` for the Cube call above and must
    # not block it on a database write.
    try:
        created, count = await asyncio.to_thread(
            library.save, engine, spec, html=html, saved_by=_caller.email, topics=topics
        )
    except library.LibraryFull as exc:
        raise HTTPException(
            409,
            f"The dashboard library is full ({exc} saved). Save over an existing "
            "dashboard by reusing its name, or ask the user which one to remove.",
        ) from exc

    log.info(
        "dashboard_saved",
        name=spec.name,
        created=created,
        source=_caller.source,
        topics=topics,
        count=count,
    )
    return SaveResult(name=spec.name, title=spec.title, created=created, count=count, topics=topics)


@dashboards_router.get(
    "/get_dashboard",
    operation_id="get_dashboard",
    summary="Read the dashboard library",
)
def get_dashboard_tool(
    _caller: Annotated[Reporter, Depends(get_reporter)],
    engine: Annotated[Engine, Depends(get_library_engine)],
    # Validated here rather than at the query: a name that cannot be a
    # `DashboardSpec.name` cannot be in the library either, and rejecting it
    # keeps an arbitrary string out of the lookup.
    name: Annotated[
        str | None,
        Query(pattern=NAME_RE.pattern, max_length=64),
    ] = None,
) -> DashboardLibrary:
    """List the published dashboards, or read one back by name.

    Call it with no `name` to see what the library already holds: every
    dashboard's name, title, description, topics and vote score, without their
    specs. Do this before building a chart from scratch — if one already
    answers the question, redraw it instead of writing a new spec, and tell the
    user it came from the library.

    Read the scores. A dashboard with a negative `score` was voted down by
    readers and `hidden` ones have dropped off the public page entirely: do not
    hand one to a user as though it were trusted work. Either fix it and save
    it back under the same name, or build a fresh chart under a new one.

    Call it with a `name` to get that dashboard's full spec back. The `spec`
    field is a `render_dashboard` payload exactly as it was saved: pass it
    straight to `render_dashboard` to draw it, or edit it first (a different
    date range, a filter, another state) and draw the edited version. Editing
    a spec for a one-off question does not mean saving it back — only
    `save_dashboard` changes the library.

    A name that is not in the library comes back as an error listing the names
    that are, so a near-miss can be retried directly.

    The stored spec carries a query, never rows, so what you draw from it is
    today's data — but its title and caption were written when it was saved,
    and a date range written into the query is the one whoever saved it chose.
    Read the query before you describe what the chart shows.
    """
    if name is None:
        entries = library.index(engine)
        return DashboardLibrary(
            count=len(entries),
            dashboards=[LibraryEntry.model_validate(entry) for entry in entries],
        )

    entry = library.by_name(engine, name)
    if entry is None:
        # The available names go back with the error on purpose: a miss is
        # nearly always a near-miss, and this turns a second lookup call into a
        # corrected retry.
        known = ", ".join(library.names(engine, limit=_NAMES_IN_ERROR)) or "none yet"
        raise HTTPException(404, f"No saved dashboard named {name!r}. Saved: {known}.")
    return DashboardLibrary(count=1, dashboards=[LibraryEntry.model_validate(entry)])
