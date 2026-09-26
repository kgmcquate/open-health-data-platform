"""The paid data API: `/v1/...`, Plus only, with monthly allowances (ADR-0030).

Everything a paying user can call from outside the hub goes through here:

    GET  /v1/cube/meta        every cube, measure and dimension     (free)
    POST /v1/cube/load        run a `CubeQuery`                     (1 Cube query)
    POST /v1/cube/sql         the SQL a `CubeQuery` compiles to     (1 Cube query)
    *    /v1/mcp/cube         the Cube MCP server (`ohdp_mcp`)      (1 MCP call per tools/call)
    *    /v1/mcp/catalog      OpenMetadata's MCP server, proxied    (1 MCP call per tools/call)

The order for every call: API key (`hub_api.api_keys`) → owner's tier, read
live → allowance (`hub_api.api_usage`) → forward. Nothing behind this is public
any more. Cube and `mcp-cube` are ClusterIP only, and OpenMetadata's own `/mcp`
is routed here by an Ingress on the catalog host (hub-api chart).

**The Cube routes take a `CubeQuery`, not Cube's raw wire format.** That's the
same bounded query model the chat agent and the MCP tools use (ARCHITECTURE.md
§6), so paying for the API doesn't widen what can be asked. Cube's
`queryRewrite` then applies the caller's tier to the row limit, because the
service token is minted with the tier read from `users`, not one the caller sent.

**The MCP endpoints are plain ASGI, not FastAPI routes,** because MCP's
streamable HTTP transport is its own protocol: JSON-RPC bodies, SSE responses,
session headers. `McpGateway` reads the body once to count `tools/call`
messages, then replays it to the real handler untouched. Protocol traffic
(`initialize`, `tools/list`, pings) is free, so connecting a client never uses
up an allowance.

In-app chat doesn't come through here. It keeps its daily quotas
(`hub_api.chat`) and its cluster-internal connections (apps/api/config/tools.yaml).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from ohdp_mcp import server as cube_mcp
from sqlalchemy.engine import Engine
from starlette._utils import get_route_path
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hub_api import api_usage
from hub_api.api_keys import ApiCaller, ApiKeyError, authenticate, get_engine, require_api_caller
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.models import CubeQuery
from ohdp_agent.render import cube_json
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

# --- Cube REST ----------------------------------------------------------------

router = APIRouter(prefix="/v1", tags=["data-api"])


def cube_client(tier: str) -> CubeClient:
    """One client per call, as everywhere else. A seam for tests."""
    return CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=tier)


@contextmanager
def _cube_errors() -> Iterator[None]:
    """Cube's own reason for a rejected query goes back to the caller, the same
    way the MCP tools surface it. A Cube that can't be reached is a 502."""
    try:
        yield
    except CubeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        log.error("cube_unreachable", error=str(exc))
        raise HTTPException(502, "The semantic layer is not reachable right now.") from exc


def _charge(engine: Engine, caller: ApiCaller, query: CubeQuery) -> None:
    detail = {"measures": list(query.measures), "dimensions": list(query.dimensions)}
    try:
        api_usage.charge(engine, caller, "cube", detail)
    except api_usage.QuotaExceeded as exc:
        raise HTTPException(429, exc.as_json(), headers={"Retry-After": _retry_after(exc)}) from exc


def _retry_after(exc: api_usage.QuotaExceeded) -> str:
    return str(max(0, int((exc.resets_at - datetime.now(UTC)).total_seconds())))


@router.get("/cube/meta")
async def cube_meta(
    caller: Annotated[ApiCaller, Depends(require_api_caller)],
) -> list[dict[str, Any]]:
    """Every cube with its measures and dimensions. Free: it's the menu, and a
    client can't write a query without reading it."""
    with _cube_errors():
        cubes = await cube_client(caller.tier).list_metrics()
    return [cube_json(cube, with_agg=True) for cube in cubes]


@router.post("/cube/load")
async def cube_load(
    query: CubeQuery,
    caller: Annotated[ApiCaller, Depends(require_api_caller)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, Any]:
    """Run a query. Returns `rows`, `row_count`, `applied_limit`, `truncated`."""
    _charge(engine, caller, query)
    with _cube_errors():
        return await cube_client(caller.tier).run_metric_query(query)


@router.post("/cube/sql")
async def cube_sql(
    query: CubeQuery,
    caller: Annotated[ApiCaller, Depends(require_api_caller)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, str]:
    """The SQL a query compiles to. Charged like a load, because Cube compiles
    it against the live model either way."""
    _charge(engine, caller, query)
    with _cube_errors():
        return {"sql": await cube_client(caller.tier).explain_query(query)}


# --- MCP ----------------------------------------------------------------------

# A JSON-RPC request body this big is not an MCP tool call.
_MAX_BODY_BYTES = 1_000_000

# The Cube MCP server, served in-process. Stateless: each POST is handled on
# its own, so no session has to stick to one pod across a rolling deploy, and
# there's no long-lived GET stream per client to hold open. JSON responses
# rather than SSE, because every Cube tool returns one result and has nothing
# to stream. main.py runs its lifespan, since a mounted app never gets one.
cube_mcp_app = cube_mcp.mcp.http_app(path="/cube", stateless_http=True, json_response=True)


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(body, status_code=status, headers=headers)


def _tool_calls(body: bytes) -> list[dict[str, Any]]:
    """The `tools/call` requests in a JSON-RPC body, batched or not. A body that
    isn't JSON gets an empty list here; the MCP handler rejects it itself."""
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return []
    messages = payload if isinstance(payload, list) else [payload]
    return [m for m in messages if isinstance(m, dict) and m.get("method") == "tools/call"]


class McpGateway:
    """Mounted at `/v1/mcp`. Authenticates, charges each `tools/call`, stamps
    the caller's tier into the scope, and hands the request on."""

    def __init__(
        self,
        engine_getter: Callable[[], Engine | None],
        *,
        cube_app: ASGIApp = cube_mcp_app,
        catalog_app: ASGIApp | None = None,
    ) -> None:
        self._engine = engine_getter
        self._routes: dict[str, tuple[api_usage.Surface, ASGIApp]] = {
            "/cube": ("cube_mcp", cube_app),
            "/catalog": ("catalog_mcp", catalog_app or proxy_catalog),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        route = self._routes.get(get_route_path(scope))
        if route is None:
            await _json(404, {"detail": "Not found."})(scope, receive, send)
            return
        surface, inner = route

        engine = self._engine()
        if engine is None:
            await _json(503, {"detail": "The user database is not available."})(
                scope, receive, send
            )
            return
        headers = dict(Request(scope).headers)
        try:
            caller = authenticate(engine, headers.get("authorization"))
        except ApiKeyError as exc:
            await _json(exc.status, {"detail": exc.message})(scope, receive, send)
            return

        body, receive = await _buffer(receive)
        if body is None:
            await _json(413, {"detail": "Request body too large."})(scope, receive, send)
            return

        calls = _tool_calls(body)
        for call in calls:
            params = call.get("params")
            tool = params.get("name") if isinstance(params, dict) else None
            try:
                api_usage.charge(engine, caller, surface, {"tool": tool})
            except api_usage.QuotaExceeded as exc:
                await _quota_response(exc, calls)(scope, receive, send)
                return

        # Read by `ohdp_mcp.server` when it mints the Cube token, so Cube's
        # `queryRewrite` sees the caller's tier. It lives in the ASGI scope,
        # which the MCP transport hands to the tool as its request, rather than
        # in a ContextVar, which wouldn't cross into the task the tool runs in.
        scope[cube_mcp.TIER_SCOPE_KEY] = caller.tier
        await inner(scope, receive, send)


def _quota_response(exc: api_usage.QuotaExceeded, calls: list[dict[str, Any]]) -> Response:
    """A single tool call over the allowance gets a JSON-RPC error with HTTP
    200, so the MCP client hands the message to the model, which can tell the
    user. Anything else gets a plain 429."""
    if len(calls) == 1 and "id" in calls[0]:
        return _json(
            200,
            {
                "jsonrpc": "2.0",
                "id": calls[0]["id"],
                "error": {"code": -32000, "message": exc.message, "data": exc.as_json()},
            },
        )
    return _json(429, exc.as_json(), {"Retry-After": _retry_after(exc)})


async def _buffer(receive: Receive) -> tuple[bytes | None, Receive]:
    """Read the whole request body, and return it with a `receive` that replays
    it to the next app as if nothing had read it. None for a body over the cap."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            # A disconnect before the body finished. Let the next app see it.
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > _MAX_BODY_BYTES:
            return None, receive
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    body = b"".join(chunks)
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


# The headers the MCP transport needs in each direction. The caller's
# `Authorization` is never forwarded, since it carries their API key. The
# upstream call carries the gateway's own bot token instead.
_FORWARD_REQUEST = (
    "accept",
    "content-type",
    "mcp-session-id",
    "mcp-protocol-version",
    "last-event-id",
)
_FORWARD_RESPONSE = ("content-type", "mcp-session-id", "cache-control")


def catalog_client() -> httpx.AsyncClient:
    """A client per proxied request, closed when its response finishes. A seam
    for tests. No read timeout, because a GET here is a long-lived event stream."""
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))


async def proxy_catalog(scope: Scope, receive: Receive, send: Send) -> None:
    """OpenMetadata's MCP endpoint, called as the read-only gateway bot and
    streamed back. Server-sent events pass through as they arrive."""
    token = settings.openmetadata_gateway_jwt
    if not token:
        await _json(503, {"detail": "The catalog MCP endpoint is not configured."})(
            scope, receive, send
        )
        return
    request = Request(scope, receive)
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQUEST}
    headers["authorization"] = f"Bearer {token}"

    client = catalog_client()
    upstream_url = f"{settings.openmetadata_url.rstrip('/')}/mcp"
    try:
        upstream = await client.send(
            client.build_request(request.method, upstream_url, headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        log.error("catalog_mcp_unreachable", error=str(exc))
        await _json(502, {"detail": "The catalog is not reachable right now."})(
            scope, receive, send
        )
        return

    if upstream.status_code in (401, 403):
        # The gateway's own bot token was refused. Passing that 401 on would
        # tell the caller their API key is bad, when it's ours.
        await upstream.aclose()
        await client.aclose()
        log.error("catalog_mcp_bot_token_refused", status=upstream.status_code)
        await _json(502, {"detail": "The catalog refused the gateway's credentials."})(
            scope, receive, send
        )
        return

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    async def stream() -> AsyncIterator[bytes]:
        async for chunk in upstream.aiter_bytes():
            yield chunk

    response = StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESPONSE},
        background=BackgroundTask(close),
    )
    await response(scope, receive, send)
