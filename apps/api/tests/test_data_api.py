"""The paid data API: API keys, monthly allowances, and the `/v1` gateway
(`hub_api.api_keys`, `hub_api.api_usage`, `hub_api.gateway`, ADR-0030).

No network. Cube is an `httpx.MockTransport` that decodes the service token it
is sent, so these tests check the tier Cube would actually see rather than
the tier we meant to send. The catalog MCP upstream is another mock transport.

What these tests protect:

  - a key is stored only as a hash, works until revoked, and belongs to one user;
  - the tier is read from `users` on every call, so a Stripe downgrade cuts
    access at once instead of at next login;
  - free callers get 402 and over-allowance callers get 429, before anything
    reaches Cube;
  - the Cube routes still accept only a `CubeQuery`;
  - over MCP, only `tools/call` costs anything, the caller's tier reaches Cube,
    and the caller's API key is never forwarded to OpenMetadata.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from ohdp_mcp import server as mcp_server
from sqlalchemy import select
from sqlalchemy.engine import Engine
from starlette.applications import Starlette
from starlette.routing import Mount

from hub_api import api_keys, api_usage, auth, db, gateway
from hub_api.main import app
from ohdp_agent.cube import CubeClient
from ohdp_shared import settings

SECRET = "test-cube-secret"
EMAIL = "analyst@example.org"

META: dict[str, Any] = {
    "cubes": [
        {
            "name": "air_quality",
            "title": "Air Quality",
            "description": "Daily OpenAQ measurements.",
            "measures": [{"name": "air_quality.avg_value", "shortTitle": "Avg", "aggType": "avg"}],
            "dimensions": [
                {"name": "air_quality.country", "shortTitle": "Country", "type": "string"}
            ],
        }
    ]
}

QUERY = {"measures": ["air_quality.avg_value"], "dimensions": ["air_quality.country"]}


# --- fixtures -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cube_api_secret", SECRET)
    monkeypatch.setattr(settings, "plus_monthly_cube_queries", 3)
    monkeypatch.setattr(settings, "plus_monthly_mcp_calls", 2)
    monkeypatch.setattr(settings, "openmetadata_gateway_jwt", "om-bot-token")
    monkeypatch.setattr(settings, "openmetadata_url", "http://openmetadata:8585")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    made = db.make_engine(f"sqlite:///{tmp_path}/api.db")
    db.ensure_schema(made)
    now = datetime.now(UTC)
    with made.begin() as connection:
        connection.execute(
            auth.users.insert().values(
                email=EMAIL, name="An Analyst", tier="plus", created_at=now, last_login_at=now
            )
        )
    app.state.engine = made
    try:
        yield made
    finally:
        app.state.engine = None


@pytest.fixture
def cube_tiers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Cube request's `tier` claim, decoded from the token Cube received."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        claims = jwt.decode(request.headers["authorization"], SECRET, algorithms=["HS256"])
        seen.append(claims["tier"])
        if request.url.path.endswith("/meta"):
            return httpx.Response(200, json=META)
        if request.url.path.endswith("/load"):
            return httpx.Response(200, json={"data": [{"air_quality.avg_value": 7.5}]})
        if request.url.path.endswith("/sql"):
            return httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
        return httpx.Response(404)

    def build(_url: str, secret: str, *, tier: str = "free", **_: Any) -> CubeClient:
        return CubeClient(
            "http://cube:4000",
            secret,
            tier=tier,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(gateway, "cube_client", lambda tier: build("", SECRET, tier=tier))
    monkeypatch.setattr(mcp_server, "CubeClient", build)
    return seen


@pytest.fixture
def key(engine: Engine) -> str:
    return api_keys.create_key(engine, user_email=EMAIL, name="laptop")[1]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _set_tier(engine: Engine, tier: str) -> None:
    auth.set_user_tier(engine, email=EMAIL, tier=tier)


# --- keys -----------------------------------------------------------------------


def test_a_key_is_stored_only_as_its_hash(engine: Engine, key: str) -> None:
    with engine.connect() as connection:
        row = connection.execute(select(api_keys.api_keys)).one()
    assert key.startswith("ohdp_")
    assert key not in {str(v) for v in row._mapping.values()}
    assert key.startswith(row.prefix)


def test_a_revoked_key_stops_working(engine: Engine, key: str) -> None:
    caller = api_keys.resolve_key(engine, key)
    assert caller is not None
    assert api_keys.revoke_key(engine, user_email="someone@else.org", key_id=caller.key_id) is False
    assert api_keys.revoke_key(engine, user_email=EMAIL, key_id=caller.key_id) is True
    assert api_keys.resolve_key(engine, key) is None


def test_browser_routes_create_list_and_revoke(engine: Engine) -> None:
    app.dependency_overrides[auth.get_current_user] = lambda: auth.User(email=EMAIL, tier="plus")
    try:
        client = TestClient(app)
        created = client.post("/api/keys", json={"name": "ci"}).json()
        assert created["key"].startswith("ohdp_")
        listed = client.get("/api/keys").json()
        assert [k["name"] for k in listed] == ["ci"]
        assert "key" not in listed[0]
        assert client.delete(f"/api/keys/{created['id']}").status_code == 204
        assert client.get("/api/keys").json() == []
    finally:
        app.dependency_overrides.clear()


def test_creating_a_key_needs_plus_in_the_database_not_the_session(engine: Engine) -> None:
    _set_tier(engine, "free")
    # The session still says plus, as it would until the next login.
    app.dependency_overrides[auth.get_current_user] = lambda: auth.User(email=EMAIL, tier="plus")
    try:
        response = TestClient(app).post("/api/keys", json={})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 402


# --- Cube REST --------------------------------------------------------------------


def test_no_key_is_a_401(engine: Engine) -> None:
    assert TestClient(app).post("/v1/cube/load", json=QUERY).status_code == 401
    bad = TestClient(app).post("/v1/cube/load", json=QUERY, headers=_bearer("ohdp_nope"))
    assert bad.status_code == 401


def test_a_downgrade_cuts_access_on_the_next_call(
    engine: Engine, key: str, cube_tiers: list[str]
) -> None:
    client = TestClient(app)
    assert client.post("/v1/cube/load", json=QUERY, headers=_bearer(key)).status_code == 200
    _set_tier(engine, "free")
    assert client.post("/v1/cube/load", json=QUERY, headers=_bearer(key)).status_code == 402
    assert cube_tiers == ["plus"]


def test_load_runs_as_the_callers_tier_and_is_counted(
    engine: Engine, key: str, cube_tiers: list[str]
) -> None:
    response = TestClient(app).post("/v1/cube/load", json=QUERY, headers=_bearer(key))
    assert response.status_code == 200
    assert response.json()["rows"] == [{"air_quality.avg_value": 7.5}]
    assert cube_tiers == ["plus"]
    assert api_usage.used_this_month(engine, EMAIL, "cube") == 1


def test_meta_is_free(engine: Engine, key: str, cube_tiers: list[str]) -> None:
    response = TestClient(app).get("/v1/cube/meta", headers=_bearer(key))
    assert response.status_code == 200
    assert response.json()[0]["cube"] == "air_quality"
    assert api_usage.used_this_month(engine, EMAIL, "cube") == 0


def test_only_a_cube_query_is_accepted(engine: Engine, key: str, cube_tiers: list[str]) -> None:
    raw_sql = {"sql": "SELECT * FROM secrets"}
    response = TestClient(app).post("/v1/cube/load", json=raw_sql, headers=_bearer(key))
    assert response.status_code == 422
    assert cube_tiers == []


def test_over_the_allowance_is_a_429_and_cube_is_not_called(
    engine: Engine, key: str, cube_tiers: list[str]
) -> None:
    client = TestClient(app)
    for _ in range(3):
        assert client.post("/v1/cube/sql", json=QUERY, headers=_bearer(key)).status_code == 200
    response = client.post("/v1/cube/load", json=QUERY, headers=_bearer(key))
    assert response.status_code == 429
    body = response.json()["detail"]
    assert (body["used"], body["allowance"], body["pool"]) == (3, 3, "cube")
    assert datetime.fromisoformat(body["resets_at"]) == api_usage.start_of_next_month()
    assert int(response.headers["retry-after"]) > 0
    assert len(cube_tiers) == 3


def test_last_months_usage_does_not_count(engine: Engine, key: str) -> None:
    caller = api_keys.resolve_key(engine, key)
    assert caller is not None
    with engine.begin() as connection:
        for _ in range(5):
            connection.execute(
                api_usage.api_usage.insert().values(
                    created_at=api_usage.start_of_month() - timedelta(seconds=1),
                    user_email=EMAIL,
                    key_id=caller.key_id,
                    surface="cube",
                    detail={},
                )
            )
    assert api_usage.used_this_month(engine, EMAIL, "cube") == 0


def test_start_of_next_month_rolls_the_year() -> None:
    december = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert api_usage.start_of_next_month(december) == datetime(2027, 1, 1, tzinfo=UTC)


# --- MCP ------------------------------------------------------------------------


def _rpc(method: str, params: dict[str, Any] | None = None, id: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _mcp_headers(key: str) -> dict[str, str]:
    return {
        **_bearer(key),
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


RUN_QUERY = _rpc("tools/call", {"name": "run_metric_query", "arguments": {"query": QUERY}})


@pytest.fixture
def catalog_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """What the gateway sent to OpenMetadata's MCP endpoint."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"ok": True}},
            headers={"mcp-session-id": "om-session", "x-internal": "drop me"},
        )

    monkeypatch.setattr(
        gateway,
        "catalog_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return sent


@pytest.fixture
def mcp(engine: Engine) -> Iterator[TestClient]:
    """The gateway mounted the way main.py mounts it, with the Cube MCP app's
    lifespan running. Not the whole hub app, whose lifespan wants model
    backends and tool connections."""

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with gateway.cube_mcp_app.router.lifespan_context(gateway.cube_mcp_app):
            yield

    test_app = Starlette(
        routes=[Mount("/v1/mcp", gateway.McpGateway(lambda: engine))], lifespan=lifespan
    )
    with TestClient(test_app) as client:
        yield client


def test_mcp_needs_a_plus_key(mcp: TestClient, engine: Engine, key: str) -> None:
    assert mcp.post("/v1/mcp/cube", json=_rpc("tools/list")).status_code == 401
    _set_tier(engine, "free")
    assert (
        mcp.post("/v1/mcp/cube", json=_rpc("tools/list"), headers=_mcp_headers(key)).status_code
        == 402
    )


def test_mcp_unknown_path_is_a_404(mcp: TestClient, key: str) -> None:
    assert (
        mcp.post("/v1/mcp/sql", json=_rpc("tools/list"), headers=_mcp_headers(key)).status_code
        == 404
    )


def test_listing_tools_is_free(mcp: TestClient, engine: Engine, key: str) -> None:
    response = mcp.post("/v1/mcp/cube", json=_rpc("tools/list"), headers=_mcp_headers(key))
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {"list_metrics", "describe_metric", "run_metric_query", "explain_query"}
    assert api_usage.used_this_month(engine, EMAIL, "mcp") == 0


def test_a_tool_call_is_charged_and_runs_as_the_callers_tier(
    mcp: TestClient, engine: Engine, key: str, cube_tiers: list[str]
) -> None:
    response = mcp.post("/v1/mcp/cube", json=RUN_QUERY, headers=_mcp_headers(key))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result.get("isError") is not True
    assert "7.5" in json.dumps(result)
    assert cube_tiers == ["plus"]
    assert api_usage.used_this_month(engine, EMAIL, "mcp") == 1


def test_a_tool_call_over_the_allowance_is_a_jsonrpc_error(
    mcp: TestClient,
    engine: Engine,
    key: str,
    cube_tiers: list[str],
    catalog_requests: list[httpx.Request],
) -> None:
    # Cube and catalog MCP share one pool of 2.
    mcp.post("/v1/mcp/cube", json=RUN_QUERY, headers=_mcp_headers(key))
    catalog_call = _rpc("tools/call", {"name": "search_metadata", "arguments": {}}, id=2)
    mcp.post("/v1/mcp/catalog", json=catalog_call, headers=_mcp_headers(key))
    response = mcp.post(
        "/v1/mcp/cube",
        json=_rpc("tools/call", {"name": "run_metric_query"}, id=7),
        headers=_mcp_headers(key),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7
    assert "Plus this month" in body["error"]["message"]
    assert len(cube_tiers) == 1


def test_catalog_proxy_swaps_the_key_for_the_bot_token(
    mcp: TestClient, key: str, catalog_requests: list[httpx.Request]
) -> None:
    call = _rpc("tools/call", {"name": "search_metadata", "arguments": {"query": "asthma"}})
    response = mcp.post(
        "/v1/mcp/catalog",
        json=call,
        headers={**_mcp_headers(key), "Mcp-Session-Id": "abc", "Cookie": "session=x"},
    )
    assert response.status_code == 200
    assert response.json()["result"] == {"ok": True}
    assert response.headers["mcp-session-id"] == "om-session"
    assert "x-internal" not in response.headers

    (sent,) = catalog_requests
    assert str(sent.url) == "http://openmetadata:8585/mcp"
    assert sent.headers["authorization"] == "Bearer om-bot-token"
    assert key not in str(sent.headers)
    assert "cookie" not in sent.headers
    assert sent.headers["mcp-session-id"] == "abc"
    assert json.loads(sent.content) == call


def test_catalog_bot_token_refused_is_not_passed_off_as_the_callers(
    mcp: TestClient, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gateway,
        "catalog_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(401))),
    )
    response = mcp.post("/v1/mcp/catalog", json=_rpc("tools/list"), headers=_mcp_headers(key))
    assert response.status_code == 502


def test_catalog_unconfigured_is_a_503(
    mcp: TestClient, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openmetadata_gateway_jwt", "")
    response = mcp.post("/v1/mcp/catalog", json=_rpc("tools/list"), headers=_mcp_headers(key))
    assert response.status_code == 503


def test_usage_route_reports_this_months_pools(
    engine: Engine, key: str, cube_tiers: list[str]
) -> None:
    TestClient(app).post("/v1/cube/load", json=QUERY, headers=_bearer(key))
    app.dependency_overrides[auth.get_current_user] = lambda: auth.User(email=EMAIL, tier="plus")
    try:
        body = TestClient(app).get("/api/keys/usage").json()
    finally:
        app.dependency_overrides.clear()
    assert body["pools"] == {
        "cube": {"used": 1, "allowance": 3},
        "mcp": {"used": 0, "allowance": 2},
    }
    assert body["tier"] == "plus"
