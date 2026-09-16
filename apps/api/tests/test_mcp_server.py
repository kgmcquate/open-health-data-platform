"""The Cube MCP server, exercised through a real MCP client.

No Cube, no Snowflake, no network: `CubeClient` is patched to a fixture, and the
client speaks to the server in-memory over FastMCP's transport. What these tests
protect is the contract Open WebUI depends on — the four tools exist, they are
the *only* four, `CubeQuery` validation still happens on our side of the wire,
and the bearer wall is on by default.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastmcp import Client

from ohdp_agent.cube import CubeClient
from ohdp_mcp import server

SECRET = "test-secret"

META: dict[str, Any] = {
    "cubes": [
        {
            "name": "air_quality",
            "title": "Air Quality",
            "description": "Daily OpenAQ measurements.",
            # Cube qualifies member names in /v1/meta. render._qualify must not
            # prefix them a second time.
            "measures": [
                {
                    "name": "air_quality.avg_value",
                    "shortTitle": "Avg Value",
                    "description": "Mean concentration.",
                    "aggType": "avg",
                }
            ],
            "dimensions": [
                {"name": "air_quality.country", "shortTitle": "Country", "type": "string"}
            ],
        }
    ]
}


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/meta"):
        return httpx.Response(200, json=META)
    if request.url.path.endswith("/load"):
        return httpx.Response(200, json={"data": [{"air_quality.avg_value": 7.5}]})
    if request.url.path.endswith("/sql"):
        return httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    return httpx.Response(404, json={"error": "no such endpoint"})


@pytest.fixture(autouse=True)
def _mock_cube(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every tool at a mock transport instead of a live Cube."""

    def build() -> CubeClient:
        return CubeClient(
            "http://cube:4000",
            SECRET,
            client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        )

    monkeypatch.setattr(server, "_client", build)


def _payload(result: Any) -> Any:
    """The tool's structured return value, whatever FastMCP version shaped it."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return json.loads(result.content[0].text)


async def test_exactly_four_tools_and_no_sql_tool() -> None:
    async with Client(server.mcp) as client:
        names = {t.name for t in await client.list_tools()}
    # The tool surface *is* the safety control (ADR-0016). A fifth tool arriving
    # here without a matching ADR is the thing this assertion is for.
    assert names == {"list_metrics", "describe_metric", "run_metric_query", "explain_query"}


async def test_list_metrics_qualifies_members_exactly_once() -> None:
    async with Client(server.mcp) as client:
        cubes = _payload(await client.call_tool("list_metrics", {}))
    assert cubes[0]["measures"][0]["name"] == "air_quality.avg_value"
    assert cubes[0]["dimensions"][0]["name"] == "air_quality.country"


async def test_describe_metric_reports_the_aggregation() -> None:
    async with Client(server.mcp) as client:
        cube = _payload(await client.call_tool("describe_metric", {"cube_name": "air_quality"}))
    assert cube["measures"][0]["agg"] == "avg"


async def test_run_metric_query_returns_rows() -> None:
    async with Client(server.mcp) as client:
        result = _payload(
            await client.call_tool(
                "run_metric_query", {"query": {"measures": ["air_quality.avg_value"]}}
            )
        )
    assert result["row_count"] == 1
    assert result["rows"][0]["air_quality.avg_value"] == 7.5


async def test_explain_query_returns_the_compiled_sql() -> None:
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "explain_query", {"query": {"measures": ["air_quality.avg_value"]}}
        )
    assert "SELECT 1" in str(_payload(result))


async def test_a_query_outside_the_schema_is_rejected_before_cube() -> None:
    """The safety property MCP must not weaken: no field carries SQL to Cube."""
    async with Client(server.mcp) as client:
        with pytest.raises(Exception):  # noqa: B017 — FastMCP's error type varies by version
            await client.call_tool(
                "run_metric_query",
                {"query": {"measures": ["air_quality.avg_value"], "sql": "DROP TABLE users"}},
            )


async def test_a_member_name_that_is_not_cube_dot_field_is_rejected() -> None:
    async with Client(server.mcp) as client:
        with pytest.raises(Exception):  # noqa: B017
            await client.call_tool(
                "run_metric_query", {"query": {"measures": ["air_quality.avg_value; DROP"]}}
            )


def test_the_server_refuses_to_start_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unauthenticated default is the kind of thing that reaches production."""
    monkeypatch.delenv("OHDP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OHDP_MCP_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(RuntimeError, match="OHDP_MCP_AUTH_TOKEN"):
        server.create_app()


def test_anonymous_is_available_but_must_be_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OHDP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OHDP_MCP_ALLOW_ANONYMOUS", "true")
    assert server.create_app() is not None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer right-token", True),
        ("bearer right-token", True),
        ("Bearer wrong-token", False),
        ("right-token", False),
        ("", False),
    ],
)
def test_bearer_check(header: str, expected: bool) -> None:
    headers = {"authorization": header} if header else {}
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    from starlette.requests import Request as StarletteRequest

    assert server._authorized(StarletteRequest(scope), "right-token") is expected
