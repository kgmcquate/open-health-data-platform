"""CubeClient against a mock transport — no Cube, no Snowflake, no network."""

from __future__ import annotations

import httpx
import pytest

from ohdp_agent.cube import CubeClient, CubeError, mint_service_token
from ohdp_agent.models import CubeQuery

SECRET = "test-secret"

META = {
    "cubes": [
        {
            "name": "air_quality",
            "title": "Air Quality",
            "description": "Daily OpenAQ measurements.",
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


def _client(handler: object) -> CubeClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return CubeClient(
        "http://cube:4000",
        SECRET,
        client=httpx.AsyncClient(transport=transport),
    )


def test_service_token_carries_the_tier() -> None:
    from jose import jwt

    claims = jwt.decode(mint_service_token(SECRET, tier="paid"), SECRET, algorithms=["HS256"])
    assert claims["tier"] == "paid"
    assert claims["exp"] > claims["iat"]


@pytest.mark.asyncio
async def test_list_metrics_parses_meta() -> None:
    cubes = await _client(lambda r: httpx.Response(200, json=META)).list_metrics()
    assert len(cubes) == 1
    assert cubes[0].name == "air_quality"
    assert cubes[0].measures[0].agg_type == "avg"
    assert cubes[0].dimensions[0].name == "air_quality.country"


@pytest.mark.asyncio
async def test_describe_metric_rejects_an_unknown_cube() -> None:
    client = _client(lambda r: httpx.Response(200, json=META))
    with pytest.raises(CubeError, match="No cube named"):
        await client.describe_metric("nope")


@pytest.mark.asyncio
async def test_run_query_retries_through_continue_wait() -> None:
    """Cube signals 'still running' with HTTP 200 and this body, not an error."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, json={"error": "Continue wait"})
        return httpx.Response(200, json={"data": [{"air_quality.avg_value": 7.1}]})

    result = await _client(handler).run_metric_query(CubeQuery(measures=["air_quality.avg_value"]))
    assert calls["n"] == 3
    assert result["row_count"] == 1
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_the_authorization_header_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=META)

    await _client(handler).list_metrics()
    assert seen["auth"].count(".") == 2  # a JWT, not a bare secret


@pytest.mark.asyncio
async def test_cube_errors_surface_their_reason() -> None:
    """The agent can often repair its own query from Cube's message."""
    handler = lambda r: httpx.Response(  # noqa: E731
        400, json={"error": "Member 'air_quality.nope' not found"}
    )
    with pytest.raises(CubeError, match="not found"):
        await _client(handler).run_metric_query(CubeQuery(measures=["air_quality.avg_value"]))


@pytest.mark.asyncio
async def test_truncation_is_reported() -> None:
    rows = [{"air_quality.avg_value": i} for i in range(5)]
    handler = lambda r: httpx.Response(200, json={"data": rows})  # noqa: E731
    result = await _client(handler).run_metric_query(
        CubeQuery(measures=["air_quality.avg_value"], limit=5)
    )
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_explain_query_returns_sql() -> None:
    handler = lambda r: httpx.Response(  # noqa: E731
        200, json={"sql": {"sql": ["SELECT avg(value) FROM marts.fct_air_quality_daily", []]}}
    )
    sql = await _client(handler).explain_query(CubeQuery(measures=["air_quality.avg_value"]))
    assert sql.startswith("SELECT avg(value)")


@pytest.mark.asyncio
async def test_the_query_body_is_nested_under_query() -> None:
    """Cube's /load expects {"query": {...}} — a flat body silently returns nothing."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": []})

    await _client(handler).run_metric_query(CubeQuery(measures=["air_quality.avg_value"]))
    assert "query" in seen
    assert seen["query"]["measures"] == ["air_quality.avg_value"]  # type: ignore[index]
