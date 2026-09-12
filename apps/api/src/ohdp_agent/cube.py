"""Execution tools over Cube Core's REST API (ADR-0016).

Cube Core ships no MCP server — that is a Cube Cloud Premium/Enterprise feature —
so these four tools are ours. The property ARCHITECTURE.md §6 depends on, that the
agent selects from a bounded set of measures and dimensions and can never express
a raw query, comes from Cube's query model (`models.CubeQuery`), not from MCP as
a transport.

Endpoints used (all under `{cube_api_url}/cubejs-api/v1`):
    GET  /meta   — every cube, measure and dimension. The agent's whole world.
    POST /load   — run a validated query.
    POST /sql    — the compiled SQL, shown to the user for provenance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from jose import jwt

from ohdp_agent.models import MAX_ROWS, CubeQuery
from ohdp_shared import get_logger

log = get_logger(__name__)

# Cube answers a still-running query with HTTP 200 and this exact body, expecting
# the client to re-issue the identical request. It is a continuation, not an error.
_CONTINUE_WAIT = "Continue wait"
_MAX_CONTINUE_WAITS = 30

TOKEN_TTL_SECONDS = 300


class CubeError(RuntimeError):
    """Cube rejected or failed a query. Safe to surface to the agent."""


@dataclass(frozen=True)
class MeasureInfo:
    """One measure as the agent sees it."""

    name: str
    title: str
    description: str
    cube: str
    agg_type: str


@dataclass(frozen=True)
class DimensionInfo:
    name: str
    title: str
    description: str
    cube: str
    type: str


@dataclass(frozen=True)
class CubeInfo:
    name: str
    title: str
    description: str
    measures: tuple[MeasureInfo, ...]
    dimensions: tuple[DimensionInfo, ...]


def mint_service_token(secret: str, *, tier: str = "free", ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Short-lived Cube security context (ARCHITECTURE.md §5).

    `tier` is what `queryRewrite` in semantic/cube/cube.js reads to apply the
    per-tier row ceiling, so it must come from the verified OIDC claim in
    hub-api — never from anything the agent or the user supplied.
    """
    now = int(time.time())
    token = jwt.encode({"tier": tier, "iat": now, "exp": now + ttl}, secret, algorithm="HS256")
    return str(token)


class CubeClient:
    """Thin async client. One per request is fine; it holds no query state."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        tier: str = "free",
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = f"{base_url.rstrip('/')}/cubejs-api/v1"
        self._secret = secret
        self._tier = tier
        self._timeout = timeout
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": mint_service_token(self._secret, tier=self._tier)}
        url = f"{self._base}{path}"
        if self._client is not None:
            response = await self._client.request(method, url, headers=headers, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code >= 400:
            # Cube puts a human-readable reason in `error`. Surface it: the agent
            # can often fix its own query from it (wrong member name, bad operator).
            detail = _error_detail(response)
            log.warning("cube_request_failed", path=path, status=response.status_code)
            raise CubeError(f"Cube returned {response.status_code}: {detail}")

        payload: dict[str, Any] = response.json()
        return payload

    async def _load_with_continuation(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /load, re-issuing while Cube reports the query is still running."""
        for attempt in range(_MAX_CONTINUE_WAITS):
            payload = await self._request("POST", "/load", json=body)
            if payload.get("error") != _CONTINUE_WAIT:
                return payload
            log.debug("cube_continue_wait", attempt=attempt)
        raise CubeError("Cube did not finish the query in time")

    # --- tools ------------------------------------------------------------

    async def list_metrics(self) -> tuple[CubeInfo, ...]:
        """Every cube, measure and dimension available. The agent's whole world.

        This is the bounded surface ARCHITECTURE.md §1.6 promises: if it is not
        in here, the agent cannot ask for it, and the honest answer is that we
        do not have the data.
        """
        payload = await self._request("GET", "/meta")
        return tuple(_parse_cube(c) for c in payload.get("cubes", []))

    async def describe_metric(self, cube_name: str) -> CubeInfo:
        """One cube in detail. Call after `list_metrics` narrows the field."""
        for cube in await self.list_metrics():
            if cube.name == cube_name:
                return cube
        raise CubeError(f"No cube named {cube_name!r}. Call list_metrics for what exists.")

    async def run_metric_query(self, query: CubeQuery) -> dict[str, Any]:
        """Run a validated query.

        `query` is already a `CubeQuery`, so validation happened at construction.
        The belt-and-braces clamp below matters anyway: a caller can build the
        model and mutate nothing, but a future refactor that widens the field
        should not silently widen the cap too.
        """
        body = query.to_cube_json()
        body["limit"] = min(query.limit, MAX_ROWS)
        payload = await self._load_with_continuation({"query": body})

        rows = payload.get("data", [])
        log.info("cube_query_ran", measures=list(query.measures), rows=len(rows))
        return {
            "rows": rows,
            "row_count": len(rows),
            # Cube reports the limit it actually applied after queryRewrite, which
            # is how the UI can honestly say "truncated at N".
            "applied_limit": _applied_limit(payload),
            "truncated": len(rows) >= min(query.limit, MAX_ROWS),
        }

    async def explain_query(self, query: CubeQuery) -> str:
        """The compiled SQL, for the provenance §6 requires every answer to show."""
        payload = await self._request("POST", "/sql", json={"query": query.to_cube_json()})
        sql = payload.get("sql", {})
        if isinstance(sql, dict):
            statement = sql.get("sql")
            # Cube returns [sql_string, params]; the string alone is what a human reads.
            if isinstance(statement, list) and statement:
                return str(statement[0])
            if isinstance(statement, str):
                return statement
        raise CubeError("Cube returned no SQL for that query")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        return str(body.get("error", body))[:500]
    return str(body)[:500]


def _applied_limit(payload: dict[str, Any]) -> int | None:
    annotation = payload.get("query")
    if isinstance(annotation, dict):
        limit = annotation.get("limit")
        if isinstance(limit, int):
            return limit
    return None


def _parse_cube(raw: dict[str, Any]) -> CubeInfo:
    name = str(raw.get("name", ""))
    return CubeInfo(
        name=name,
        title=str(raw.get("title", name)),
        description=str(raw.get("description", "")),
        measures=tuple(
            MeasureInfo(
                name=str(m.get("name", "")),
                title=str(m.get("shortTitle", m.get("title", ""))),
                description=str(m.get("description", "")),
                cube=name,
                agg_type=str(m.get("aggType", m.get("type", ""))),
            )
            for m in raw.get("measures", [])
        ),
        dimensions=tuple(
            DimensionInfo(
                name=str(d.get("name", "")),
                title=str(d.get("shortTitle", d.get("title", ""))),
                description=str(d.get("description", "")),
                cube=name,
                type=str(d.get("type", "")),
            )
            for d in raw.get("dimensions", [])
        ),
    )
