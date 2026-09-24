"""The saved-dashboard library (`save_dashboard` / `get_dashboard`).

`test_dashboard.py` covers the spec and the one-turn embed; this file covers
what happens when a spec is kept. Three properties are load-bearing:

  - **A saved dashboard draws.** The query is run and the chart rendered
    before anything is written, so the library cannot fill with specs that
    fail the first time someone opens them.
  - **The stored spec is a `render_dashboard` payload.** What `get_dashboard`
    returns goes straight back into `render_dashboard` — if that round trip
    breaks, the library is a write-only archive.
  - **Rows are never stored.** A saved dashboard holds a question, not an
    answer, which is what stops it going stale.

The engine lives on `tools_app.state`, not `app.state`: a mounted sub-app
resets `scope["app"]` to itself, so that is where the routes look (see
`hub_api.dashboards.get_library_engine`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.mcp import MCPToolset
from sqlalchemy.engine import Engine

from hub_api import dashboards, db, issues, library, tool_connections
from hub_api.main import app
from ohdp_agent.cube import CubeError
from ohdp_shared import settings

SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "description": "Average share of ED visits, by week.",
    "caption": "All ages, all causes.",
    "query": {
        "measures": ["ed_visits.avg_percent"],
        "time_dimensions": [{"dimension": "ed_visits.week_end", "granularity": "week"}],
    },
    "vega_lite": {
        "mark": "line",
        "encoding": {
            "x": {"field": "ed_visits.week_end", "type": "temporal"},
            "y": {"field": "ed_visits.avg_percent", "type": "quantitative"},
        },
    },
}

ROWS = [
    {"ed_visits.week_end.week": "2026-01-05", "ed_visits.avg_percent": 3.1},
    {"ed_visits.week_end.week": "2026-01-12", "ed_visits.avg_percent": 3.6},
]


@pytest.fixture
def cube(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cube answers with `ROWS` — saving runs the query for real."""

    async def fake_query(self: object, query: object) -> dict[str, Any]:
        return {"rows": ROWS, "row_count": len(ROWS), "truncated": False, "applied_limit": 1000}

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fake_query)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A signed-in browser caller, against a real (SQLite) library."""
    engine: Engine = db.make_engine(f"sqlite:///{tmp_path}/library.db")
    db.ensure_schema(engine)
    issues.tools_app.state.engine = engine
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="web", email="a@b.test"
    )
    try:
        yield TestClient(app)
    finally:
        issues.tools_app.dependency_overrides.clear()
        issues.tools_app.state.engine = None


def test_saving_returns_the_name_and_that_it_was_created(client: TestClient, cube: None) -> None:
    response = client.post("/tools/save_dashboard", json=SPEC)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ed-visits"
    assert body["created"] is True
    assert body["count"] == 1


def test_a_saved_spec_round_trips_into_render_dashboard(client: TestClient, cube: None) -> None:
    """The point of the library: what comes back out is a drawable payload."""
    client.post("/tools/save_dashboard", json=SPEC)

    spec = client.get("/tools/get_dashboard", params={"name": "ed-visits"}).json()["dashboards"][0][
        "spec"
    ]

    assert spec["query"] == SPEC["query"]
    redrawn = client.post("/tools/render_dashboard", json=spec)
    assert redrawn.status_code == 200
    assert redrawn.headers["content-disposition"] == "inline"


def test_no_rows_are_stored_with_the_spec(client: TestClient, cube: None) -> None:
    """A saved dashboard holds a question, not an answer — so it cannot
    disagree with the semantic layer a month later."""
    client.post("/tools/save_dashboard", json=SPEC)

    entry = client.get("/tools/get_dashboard", params={"name": "ed-visits"}).json()["dashboards"][0]

    assert "3.1" not in str(entry)
    assert "data" not in entry["spec"]["vega_lite"]


def test_the_index_lists_names_without_their_specs(client: TestClient, cube: None) -> None:
    """A listing carrying every Vega-Lite spec is kilobytes the model will not
    read; the specs are one call away by name."""
    client.post("/tools/save_dashboard", json=SPEC)
    client.post("/tools/save_dashboard", json={**SPEC, "name": "ed-visits-by-state"})

    body = client.get("/tools/get_dashboard").json()

    assert body["count"] == 2
    assert {entry["name"] for entry in body["dashboards"]} == {
        "ed-visits",
        "ed-visits-by-state",
    }
    assert all(entry["spec"] is None for entry in body["dashboards"])
    assert all(entry["description"] for entry in body["dashboards"])


def test_saving_the_same_name_overwrites_rather_than_duplicating(
    client: TestClient, cube: None
) -> None:
    """A model that saves a name twice is correcting a chart, not filing a
    second one — the name is the library's key."""
    client.post("/tools/save_dashboard", json=SPEC)

    response = client.post("/tools/save_dashboard", json={**SPEC, "title": "ED visits, revised"})

    assert response.status_code == 200
    assert response.json()["created"] is False
    body = client.get("/tools/get_dashboard").json()
    assert body["count"] == 1
    assert body["dashboards"][0]["title"] == "ED visits, revised"


def test_a_spec_that_cannot_be_drawn_is_not_saved(client: TestClient, cube: None) -> None:
    """Validation is by doing: the chart is drawn before it is stored, so the
    model gets the same correctable reason a failed render gives it."""
    bad = {
        **SPEC,
        "vega_lite": {
            "mark": "line",
            "encoding": {"y": {"field": "ed_visits.absent", "type": "quantitative"}},
        },
    }

    response = client.post("/tools/save_dashboard", json=bad)

    assert response.status_code == 422
    assert "ed_visits.absent" in response.json()["detail"]
    assert client.get("/tools/get_dashboard").json()["count"] == 0


def test_a_rejected_query_is_not_saved(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(self: object, query: object) -> dict[str, Any]:
        raise CubeError("Member 'ed_visits.nope' not found")

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fail)

    response = client.post("/tools/save_dashboard", json=SPEC)

    assert response.status_code == 400
    assert "ed_visits.nope" in response.json()["detail"]
    assert client.get("/tools/get_dashboard").json()["count"] == 0


def test_an_unknown_name_comes_back_with_the_names_that_do_exist(
    client: TestClient, cube: None
) -> None:
    """A miss is nearly always a near-miss; listing the real names turns a
    second lookup call into a corrected retry."""
    client.post("/tools/save_dashboard", json=SPEC)

    response = client.get("/tools/get_dashboard", params={"name": "ed-visit"})

    assert response.status_code == 404
    assert "ed-visits" in response.json()["detail"]


def test_a_full_library_refuses_rather_than_evicting(
    client: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling is a spam guard, not a cache: nothing a user asked to keep
    is dropped to make room."""
    monkeypatch.setattr(library, "MAX_DASHBOARDS", 1)
    client.post("/tools/save_dashboard", json=SPEC)

    response = client.post("/tools/save_dashboard", json={**SPEC, "name": "another-chart"})

    assert response.status_code == 409
    assert client.get("/tools/get_dashboard").json()["count"] == 1
    # The name already in the library is still savable — the ceiling is on new
    # entries, not on correcting one that exists.
    assert client.post("/tools/save_dashboard", json=SPEC).status_code == 200


def test_the_library_needs_a_credential(tmp_path: Path) -> None:
    """Same wall as `render_dashboard`: a session cookie or the tools bearer."""
    unauthenticated = TestClient(app)

    assert unauthenticated.post("/tools/save_dashboard", json=SPEC).status_code == 401
    assert unauthenticated.get("/tools/get_dashboard").status_code == 401


def test_both_operations_are_in_the_spec_the_model_reads(client: TestClient) -> None:
    """The `ohdp-tools` toolset is built from this spec — an operation missing
    here is a tool the model does not have."""
    paths = client.get("/tools/openapi.json").json()["paths"]

    assert "post" in paths["/save_dashboard"]
    assert "get" in paths["/get_dashboard"]


def test_an_unavailable_database_is_a_503_not_an_empty_library(cube: None) -> None:
    """ "Nothing saved" and "the database is down" must not look the same, or
    the model will tell a user their dashboard is gone."""
    issues.tools_app.state.engine = None
    issues.tools_app.dependency_overrides[issues.get_reporter] = lambda: issues.Reporter(
        source="chatbot", email=None
    )
    try:
        client = TestClient(app)
        assert client.get("/tools/get_dashboard").status_code == 503
    finally:
        issues.tools_app.dependency_overrides.clear()


async def test_get_dashboard_reaches_the_model_as_a_tool_not_a_resource(
    client: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_dashboard` is the library's only read path, and it is a GET.

    FastMCP's OpenAPI conversion has historically mapped GET operations to MCP
    *resources* rather than tools, and a resource is not something this agent
    ever calls — the library would be write-only, with nothing raising
    anywhere. This asserts the whole wrapper `hub_api.main` builds at startup,
    not just the FastAPI route: both halves of the library have to arrive as
    callable tools.
    """
    monkeypatch.setattr(settings, "tools_auth_token", "tools-token", raising=False)
    client.post("/tools/save_dashboard", json=SPEC)

    toolset = cast(
        MCPToolset,
        tool_connections.build_local_openapi_toolset(
            app,
            issues.tools_app,
            id="ohdp-tools",
            mount_path="/tools",
            headers={"Authorization": "Bearer tools-token"},
        ),
    )

    async with toolset.client as server:
        assert {"save_dashboard", "get_dashboard"} <= {t.name for t in await server.list_tools()}
        result = await server.call_tool("get_dashboard", {"name": "ed-visits"})

    # `structured_content`, not text: the model gets a spec it can hand back to
    # `render_dashboard`, which is the library's whole contract.
    assert result.structured_content["dashboards"][0]["spec"]["query"] == SPEC["query"]


async def test_a_refresh_re_renders_from_the_stored_spec(client: TestClient, cube: None) -> None:
    """The background half of staleness: the page is rebuilt from the spec, so
    a refresh can never invent content the spec does not describe."""
    client.post("/tools/save_dashboard", json=SPEC)
    engine = issues.tools_app.state.engine
    entry = library.by_name(engine, "ed-visits")
    assert entry is not None
    library.store_render(engine, name="ed-visits", html="<html>stale</html>")

    await dashboards.refresh_render(engine, "ed-visits", entry["spec"])

    stored = library.rendered_page(engine, "ed-visits")
    assert stored is not None
    assert "vegaEmbed" in stored[0]
    refreshed = library.by_name(engine, "ed-visits")
    assert refreshed is not None and refreshed["stale"] is False


async def test_a_refresh_that_fails_leaves_the_old_page_in_place(
    client: TestClient, cube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It runs with nobody listening, after the response has gone out — so a
    Cube outage must cost the refresh, not the dashboard."""
    client.post("/tools/save_dashboard", json=SPEC)
    engine = issues.tools_app.state.engine
    entry = library.by_name(engine, "ed-visits")
    assert entry is not None
    library.store_render(engine, name="ed-visits", html="<html>older but fine</html>")

    async def fail(self: object, query: object) -> dict[str, Any]:
        raise CubeError("cube is down")

    monkeypatch.setattr("ohdp_agent.cube.CubeClient.run_metric_query", fail)

    await dashboards.refresh_render(engine, "ed-visits", entry["spec"])

    stored = library.rendered_page(engine, "ed-visits")
    assert stored is not None
    assert stored[0] == "<html>older but fine</html>"
