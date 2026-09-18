"""Render a dashboard into the chat turn itself (ADR-0025).

Open WebUI renders a tool result as an interactive iframe when the tool server
answers with `Content-Type: text/html` **and** `Content-Disposition: inline` —
its Rich UI embed path, in core since 0.6.31 and reached by external OpenAPI
tool servers as well as by its own Python ones. That is why these operations
live here, on hub-api's `/tools` app, and not in `ohdp_mcp` alongside the other
Cube tools: MCP results are JSON-RPC content with no HTTP response headers, so
an MCP tool has no way to ask for an embed. The transport is the reason for the
placement, and it is the same split ADR-0016 already made for `report_issue`.

The model writes its Vega-Lite, but never the data. It sends a
`DashboardSpec` — a title, one Cube query, and a `vega` spec — and this
module runs the query and binds the rows (`ohdp_agent.dashboard`). A `data`
key anywhere in a `vega` spec is rejected before it can reach a browser.
Two consequences worth stating plainly:

  - **The numbers cannot be invented.** Rows reach the page from Cube, through
    the same validated `CubeQuery` every other execution tool uses.
  - **The output is deterministic.** The same spec renders the same page, so a
    dashboard is reviewable as code rather than as a screenshot.

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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from hub_api.issues import Reporter, get_reporter
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.dashboard import ChartData, DashboardSpec, render_html
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# Everyone reaching either chat surface is tier `free` (ARCHITECTURE.md §5), and
# tier is what Cube's `queryRewrite` reads to apply its row ceiling — so it is
# set here and never taken from the caller. Same reasoning as `ohdp_mcp.server`.
TIER = "free"

# Rendering an embed means holding the chart's rows in memory and handing Open
# WebUI a single response.
_QUERY_TIMEOUT_SECONDS = 90.0

# The header pair that turns a tool result into an iframe instead of a wall of
# markup in the transcript. Both are required; `Content-Type` comes from
# HTMLResponse itself.
_EMBED_HEADERS = {"Content-Disposition": "inline"}

dashboards_router = APIRouter()


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
    """Draw a chart of semantic-layer data directly in this chat.

    Use this whenever a question is better answered by a picture than by a
    table, and after `run_metric_query` has shown you the actual numbers — this
    tool runs its own query and does not report the rows back to you, so plan
    and describe the data first, then draw it.

    The spec carries a `query` in exactly the form `run_metric_query` takes,
    plus a `vega` Vega-Lite spec describing how to draw that query's rows. The
    spec may use anything Vega-Lite supports — `mark`, `encoding`, `transform`,
    `layer`, `params` — but must not include a `data` key anywhere: rows are
    bound from `query` by the server. Use column names exactly as they came back
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
