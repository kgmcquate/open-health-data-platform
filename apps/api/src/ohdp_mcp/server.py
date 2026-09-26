"""A FastMCP server over Cube Core's REST API (ADR-0017).

Cube Core ships no MCP server — that is a Cube Cloud Premium/Enterprise feature
(ADR-0016) — so nothing outside the in-process tool-use loop can talk to the
semantic layer without one. This module is that server, and it is deliberately
*thin*: every tool delegates to `ohdp_agent.cube.CubeClient`, the same code path
the in-process chat agent uses.

**What is and is not a security boundary here.** The property ARCHITECTURE.md §6
depends on is that a caller selects from a bounded set of measures and dimensions
and can never express a raw query. That property comes from `models.CubeQuery`,
which is validated on this side of the wire, so it holds for an MCP client
exactly as it holds for the tool-use loop. What MCP does *not* give us is the
quota gate and the per-turn logging hub-api applies before invoking a model —
those live in `hub_api.chat`, not here. That is why this server is ClusterIP-only
with no Ingress: staying off the internet is the access control, and this
server trusts its caller (hub-api's own chat agent, over the `mcp-cube`
connection in apps/api/config/tools.yaml).

`OHDP_MCP_AUTH_TOKEN` adds a static bearer check on top of that, so a pod that
wanders into the namespace still cannot mint Cube queries. It is defence in
depth, not the primary control, and the server refuses to start without it
unless `OHDP_MCP_ALLOW_ANONYMOUS` is set — an unauthenticated default is the
kind of thing that survives to production by accident.

Transport is streamable HTTP at `/mcp`, which is what pydantic-ai's `MCPToolset`
speaks.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.models import CubeQuery
from ohdp_agent.render import cube_json, index_json, search_cubes
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

MCP_PATH = "/mcp"

# Everyone reaching this server is tier `free` — the same assumption
# hub_api.chat records for hub-api's own agent, and for the same reason:
# ARCHITECTURE.md §5's IdP with a real `tier` claim does not exist yet. The tier
# is what Cube's `queryRewrite` reads to apply the row ceiling, so it must never
# come from the caller.
TIER = os.environ.get("OHDP_MCP_TIER", "free")

mcp: FastMCP[None] = FastMCP(
    name="ohdp-cube",
    instructions=(
        "The semantic layer over the Open Health Data Platform warehouse. "
        "Call list_metrics first, with a few topic keywords as `search` — it "
        "returns the matching cubes and their measure and dimension names. If "
        "nothing matches even after trying synonyms, the platform does not have "
        "that data, and saying so is the correct answer; there is no "
        "SQL tool and no way to reach the warehouse directly. Members are always "
        "'cube_name.field_name' exactly as list_metrics returned them. After "
        "running a query, call explain_query on the same query and show the "
        "compiled SQL: every number this platform reports has to be traceable. "
        "These results are population-level public health data, not clinical "
        "decision support."
    ),
)


def _client() -> CubeClient:
    """One client per call. It holds no query state, and the token it mints is
    short-lived by design (`cube.TOKEN_TTL_SECONDS`)."""
    return CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=TIER)


@asynccontextmanager
async def _surfacing_cube_errors() -> AsyncIterator[None]:
    """Let Cube's own reason reach the caller.

    FastMCP masks unexpected exceptions to "Error executing tool ..." — sensible
    for an unknown failure, wrong for this one. Cube puts a human-readable reason
    in its error (a wrong member name, an unsupported operator), and that reason
    is usually enough for the model to fix its own query. `ToolError` is the
    exception class FastMCP passes through verbatim.
    """
    try:
        yield
    except CubeError as exc:
        raise ToolError(str(exc)) from exc


# --- tools ----------------------------------------------------------------
#
# Docstrings are the tool descriptions the model sees; FastMCP derives the input
# schemas from the annotations. `run_metric_query` and `explain_query` take a
# `CubeQuery`, so the schema the caller is shown and the model we actually accept
# are the same object — they cannot drift.


@mcp.tool
async def list_metrics(search: str | None = None) -> list[dict[str, Any]]:
    """Cubes in the semantic layer, with their measure and dimension names.

    Always pass `search`: a few keywords for the topic (e.g. "asthma
    hospitalization", "air quality pm25"). Any cube whose name, description or
    members contain any keyword is returned, best match first. Omit it only
    when you genuinely need the whole catalog. An empty result means no cube
    mentions those words — try synonyms or broader terms before concluding the
    platform lacks the data. Call describe_metric on a cube for member
    descriptions and types.
    """
    async with _surfacing_cube_errors():
        return index_json(search_cubes(await _client().list_metrics(), search))


@mcp.tool
async def describe_metric(cube_name: str) -> dict[str, Any]:
    """One cube in detail: its measures, dimensions, types and descriptions.

    Call after list_metrics narrows the field. `cube_name` must be a cube name
    exactly as list_metrics returned it.
    """
    async with _surfacing_cube_errors():
        return cube_json(await _client().describe_metric(cube_name), with_agg=True)


@mcp.tool
async def run_metric_query(query: CubeQuery) -> dict[str, Any]:
    """Run a query against the semantic layer and return the rows.

    Members are 'cube_name.field_name' exactly as list_metrics returned them.
    There is no SQL here and no way to express one: anything outside this schema
    is rejected before the query reaches Cube. The result reports `row_count` and
    whether it was `truncated` at the row cap — say so if it was.
    """
    async with _surfacing_cube_errors():
        return await _client().run_metric_query(query)


@mcp.tool
async def explain_query(query: CubeQuery) -> str:
    """The compiled SQL for a query, so the reader can see where a number came from.

    Call this on the same query you just ran and show the result alongside the
    answer.
    """
    async with _surfacing_cube_errors():
        return await _client().explain_query(query)


# --- transport ------------------------------------------------------------


class BearerTokenMiddleware:
    """A static bearer check in front of the MCP app.

    Written as plain ASGI rather than with FastMCP's own auth providers on
    purpose: those are built around OAuth/JWT verification and their API has
    moved between FastMCP releases, whereas this is one string comparison whose
    behaviour is obvious from reading it. `compare_digest` because the token is
    a shared secret, not a public identifier.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request = Request(scope)
        if not _authorized(request, self._token):
            log.warning("mcp_unauthorized", path=scope.get("path"))
            response: Response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(presented.strip(), token)


def create_app() -> ASGIApp:
    """The ASGI app: MCP at /mcp, plus an unauthenticated /healthz for the kubelet.

    Run it with `uvicorn ohdp_mcp.server:create_app --factory`, which is what the
    image's CMD does — the token check has to be decided at startup, not import
    time, so there is no module-level `app` to point at.
    """
    token = os.environ.get("OHDP_MCP_AUTH_TOKEN", "")
    allow_anonymous = os.environ.get("OHDP_MCP_ALLOW_ANONYMOUS", "").lower() in {"1", "true", "yes"}
    if not token and not allow_anonymous:
        raise RuntimeError(
            "OHDP_MCP_AUTH_TOKEN is unset. Set it, or set OHDP_MCP_ALLOW_ANONYMOUS=true "
            "to run this server with no authentication (local development only)."
        )
    if not token:
        log.warning("mcp_anonymous_access_enabled")

    # FastMCP returns a Starlette app carrying the lifespan its session manager
    # needs; wrap it, never replace it.
    inner: ASGIApp = mcp.http_app(path=MCP_PATH)
    guarded: ASGIApp = BearerTokenMiddleware(inner, token) if token else inner

    async def app(scope: Any, receive: Any, send: Any) -> None:
        # The lifespan scope must reach the MCP app unguarded, or the session
        # manager never starts and every request 500s.
        if scope["type"] == "lifespan":
            await inner(scope, receive, send)
            return
        # A probe that has to carry a secret is a probe that fails the day the
        # secret rotates, so /healthz sits outside the bearer check.
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            response: Response = JSONResponse({"status": "ok"})
            await response(scope, receive, send)
            return
        await guarded(scope, receive, send)

    return app
