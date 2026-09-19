"""Config-driven MCP/OpenAPI tool connections (hub_api.tool_connections) —
parsing, `${VAR}` resolution, and the OpenAPI-spec-to-toolset path, against a
mock transport rather than a real server.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from pydantic_ai.mcp import MCPToolset

from hub_api import tool_connections as tc

# Captured before any test patches `tc.httpx2.AsyncClient` — the patch
# replaces that same attribute on the shared `httpx2` module, so a
# replacement that itself called `httpx2.AsyncClient(...)` would recurse into
# its own patched self rather than the real client.
_RealAsyncClient = httpx2.AsyncClient

PETSTORE_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "petstore", "version": "1"},
    "paths": {
        "/pets": {
            "get": {
                "operationId": "list_pets",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def test_load_config_parses_both_connection_types(tmp_path: Path) -> None:
    path = tmp_path / "tools.yaml"
    path.write_text(
        """
connections:
  - id: weather
    type: mcp
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer ${WEATHER_TOKEN}"
  - id: petstore
    type: openapi
    spec_url: https://petstore.example.com/openapi.json
    base_url: https://petstore.example.com
"""
    )
    config = tc.load_config(path)

    assert len(config.connections) == 2
    weather, petstore = config.connections
    assert isinstance(weather, tc.McpConnection)
    assert weather.headers == {"Authorization": "Bearer ${WEATHER_TOKEN}"}
    assert isinstance(petstore, tc.OpenApiConnection)
    assert petstore.base_url == "https://petstore.example.com"


def test_load_config_missing_file_is_empty(tmp_path: Path) -> None:
    assert tc.load_config(tmp_path / "does-not-exist.yaml").connections == []


def test_env_ref_resolves_against_env_file_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "env_file_values", lambda: {"WEATHER_TOKEN": "secret-123"})
    assert tc._resolve("Bearer ${WEATHER_TOKEN}") == "Bearer secret-123"


def test_env_ref_unresolved_becomes_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "env_file_values", lambda: {})
    assert tc._resolve("Bearer ${MISSING}") == "Bearer "


async def test_build_openapi_toolset_from_a_fetched_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spec is fetched once, then handed to FastMCP.from_openapi — this
    exercises that whole path, not just the parsing."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=PETSTORE_SPEC)

    monkeypatch.setattr(
        tc.httpx2,
        "AsyncClient",
        lambda **kwargs: _RealAsyncClient(
            transport=httpx2.MockTransport(handler), **{k: v for k, v in kwargs.items()}
        ),
    )
    conn = tc.OpenApiConnection(
        id="petstore",
        type="openapi",
        spec_url="https://petstore.example.com/openapi.json",
        base_url="https://petstore.example.com",
    )

    toolset = await tc._build_openapi_toolset(conn)

    assert isinstance(toolset, MCPToolset)


async def test_load_tool_connections_skips_a_connection_that_fails_to_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    monkeypatch.setattr(
        tc.httpx2,
        "AsyncClient",
        lambda **kwargs: _RealAsyncClient(
            transport=httpx2.MockTransport(failing_handler),
            **{k: v for k, v in kwargs.items()},
        ),
    )
    # `_build_mcp_toolset` makes no connection at construction time — only the
    # broken OpenAPI fetch above should be reachable here.
    good = tc.McpConnection(id="weather", type="mcp", url="https://example.com/mcp")
    bad = tc.OpenApiConnection(
        id="broken",
        type="openapi",
        spec_url="https://broken.example.com/openapi.json",
        base_url="https://broken.example.com",
    )
    config = tc.ToolsConfig(connections=[good, bad])

    toolsets = await tc.load_tool_connections(config)

    assert set(toolsets) == {"weather"}
