"""Render a dashboard into the chat turn itself (ADR-0025).

Open WebUI renders a tool result as an interactive iframe when the tool server
answers with `Content-Type: text/html` **and** `Content-Disposition: inline` —
its Rich UI embed path, in core since 0.6.31 and reached by external OpenAPI
tool servers as well as by its own Python ones. That is why these operations
live here, on hub-api's `/tools` app, and not in `ohdp_mcp` alongside the other
Cube tools: MCP results are JSON-RPC content with no HTTP response headers, so
an MCP tool has no way to ask for an embed. The transport is the reason for the
placement, and it is the same split ADR-0016 already made for `report_issue`.

The model never writes HTML and never writes Vega-Lite. It sends a
`DashboardSpec` — titles, Cube queries, and which column goes on which axis —
and this module runs the queries and compiles the page (`ohdp_agent.dashboard`).
Three consequences worth stating plainly:

  - **The numbers cannot be invented.** Rows reach the page from Cube, through
    the same validated `CubeQuery` every other execution tool uses.
  - **The output is deterministic.** The same spec renders the same page, so a
    dashboard is reviewable as code rather than as a screenshot.
  - **The spec is what gets committed.** A rendered card carries its own YAML,
    and `ohdp_agent/dashboards/` holds the ones that were kept — which
    `open_saved_dashboard` re-runs against current data.

Saved specs live inside the package rather than in a repo-root directory
because `apps/api/Dockerfile` copies only the built virtualenv into the runtime
stage; a top-level `dashboards/` would be reviewable in git and absent from the
image, which is the worst of both.

One limitation to know before extending this. Open WebUI's `process_tool_result`
only accepts the "(embed, context-for-the-model)" pair from tools it runs
in-process; for an external server the HTTP body is the embed, and the model
gets a fixed "Embedded UI result is active and visible to the user" string.
So the model cannot read the rows back out of a render, and the prompt tells it
to run `run_metric_query` first when it intends to say anything *about* the
data. Rendering twice is cheap — every cube is pre-aggregated (ADR-0024).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from hub_api.issues import Reporter, get_reporter
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.dashboard import DashboardSpec, PanelData, render_html
from ohdp_agent.dashboards import list_saved, load_saved
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# Everyone reaching either chat surface is tier `free` (ARCHITECTURE.md §5), and
# tier is what Cube's `queryRewrite` reads to apply its row ceiling — so it is
# set here and never taken from the caller. Same reasoning as `ohdp_mcp.server`.
TIER = "free"

# Rendering an embed means holding every panel's rows in memory at once and
# handing Open WebUI a single response, so the panels are fetched together
# rather than one after another. Six concurrent pre-aggregated queries is a
# smaller load than the chat surface's own polling.
_QUERY_TIMEOUT_SECONDS = 90.0

# The header pair that turns a tool result into an iframe instead of a wall of
# markup in the transcript. Both are required; `Content-Type` comes from
# HTMLResponse itself.
_EMBED_HEADERS = {"Content-Disposition": "inline"}

dashboards_router = APIRouter()


class SavedDashboard(BaseModel):
    name: str
    title: str
    description: str | None = None
    panel_count: int


def _client() -> CubeClient:
    return CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=TIER)


async def _run_panels(spec: DashboardSpec) -> list[PanelData]:
    """Fetch every panel's rows, or fail the whole render with Cube's reason.

    Cube's own message names the wrong member or the unsupported operator, and
    that message is usually enough for the model to correct its spec on the next
    attempt — so it is surfaced rather than replaced with a generic failure.
    """
    client = _client()

    async def one(index: int) -> PanelData:
        result = await client.run_metric_query(spec.panels[index].query)
        return PanelData(
            rows=result["rows"],
            row_count=result["row_count"],
            truncated=bool(result["truncated"]),
        )

    try:
        async with asyncio.timeout(_QUERY_TIMEOUT_SECONDS):
            return list(await asyncio.gather(*(one(i) for i in range(len(spec.panels)))))
    except CubeError as exc:
        raise HTTPException(400, f"The semantic layer rejected a panel query: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(
            504,
            "The semantic layer did not answer in time. Try fewer panels, a "
            "shorter date range, or a coarser granularity.",
        ) from exc


def _embed(spec: DashboardSpec, data: list[PanelData]) -> HTMLResponse:
    return HTMLResponse(content=render_html(spec, data), headers=_EMBED_HEADERS)


@dashboards_router.post(
    "/render_dashboard",
    operation_id="render_dashboard",
    summary="Draw a dashboard in the chat",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "The rendered dashboard"}},
)
async def render_dashboard_tool(
    spec: DashboardSpec,
    # The identity check on hub-api's /tools app: an oauth2-proxy header from a
    # browser, or Open WebUI's shared bearer. The caller's identity is not used
    # here — rendering is read-only and the data is public — but an
    # unauthenticated route on this mount would be a Cube query anyone in the
    # cluster could run.
    _caller: Annotated[Reporter, Depends(get_reporter)],
) -> HTMLResponse:
    """Draw one or more charts of semantic-layer data directly in this chat.

    Use this whenever a question is better answered by a picture than by a
    table, and after `run_metric_query` has shown you the actual numbers — this
    tool runs its own queries and does not report the rows back to you, so plan
    and describe the data first, then draw it.

    Each panel carries a `query` in exactly the form `run_metric_query` takes,
    plus a `chart` saying which returned column goes on which axis. `x` is
    always the category or time column and `y` is always the measure, including
    for `horizontal_bar`. Use column names exactly as they came back in the
    query result. Pick the form from the data: a time series is a `line`, a
    measure across a handful of named categories is a `horizontal_bar`, a
    part-of-whole is a `stacked_bar`. Give every axis a title that carries its
    units, and title the dashboard with the filter scope it was run at.

    Keep it to what a reader can take in — at most six panels, and aggregate to
    a top-N plus an "Other" bucket rather than plotting dozens of categories.
    The rendered card shows its own data table, the compiled query behind each
    panel, and the dashboard's YAML source, so you do not need to repeat any of
    those in your reply. Tell the user the source is in the card if they want to
    keep the dashboard.
    """
    data = await _run_panels(spec)
    log.info("dashboard_rendered", name=spec.name, panels=len(spec.panels))
    return _embed(spec, data)


@dashboards_router.get(
    "/saved_dashboards",
    operation_id="list_saved_dashboards",
    summary="List the saved dashboards",
)
async def list_saved_dashboards_tool(
    _caller: Annotated[Reporter, Depends(get_reporter)],
) -> list[SavedDashboard]:
    """The dashboards that have been kept in this repository and can be reopened.

    These are curated and reviewed, so prefer one of them over building a new
    dashboard when it answers the question. Open one with
    `open_saved_dashboard`; it re-runs against current data every time.
    """
    return [
        SavedDashboard(
            name=spec.name,
            title=spec.title,
            description=spec.description,
            panel_count=len(spec.panels),
        )
        for spec in list_saved()
    ]


@dashboards_router.get(
    "/saved_dashboards/{name}",
    operation_id="open_saved_dashboard",
    summary="Open a saved dashboard",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "The rendered dashboard"}},
)
async def open_saved_dashboard_tool(
    name: Annotated[str, Path(description="The dashboard's `name`, from list_saved_dashboards")],
    _caller: Annotated[Reporter, Depends(get_reporter)],
) -> HTMLResponse:
    """Draw a saved dashboard in this chat, against current data.

    A saved dashboard stores its questions rather than its answers, so what you
    get back is today's numbers, not the numbers from when it was written.
    """
    try:
        spec = load_saved(name)
    except KeyError as exc:
        available = ", ".join(s.name for s in list_saved()) or "none"
        raise HTTPException(
            404, f"No saved dashboard named {name!r}. Available: {available}."
        ) from exc

    data = await _run_panels(spec)
    log.info("saved_dashboard_opened", name=spec.name)
    return _embed(spec, data)
