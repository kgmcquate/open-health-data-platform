"""OpenMetadata MCP client (ohdp_agent.catalog).

The fixtures here are trimmed copies of what the live 2.0.1 deployment actually
returned during M3.0 — including the two failure shapes that matter and are easy
to get wrong: a tool error arriving as HTTP 200 with `isError: true`, and the
write tools being advertised right alongside the read ones.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ohdp_agent.catalog import READ_ONLY_TOOLS, CatalogClient, CatalogError


def _mock(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _rpc_ok(result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


TOOLS_PAYLOAD = {
    "tools": [
        {
            "name": "search_metadata",
            "description": "Keyword search.",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "get_asset_context",
            "description": "One asset.",
            "inputSchema": {"type": "object"},
        },
        # Advertised by the real server; must never reach the model.
        {
            "name": "patch_entity",
            "description": "Edit an entity.",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "create_glossary",
            "description": "Create a glossary.",
            "inputSchema": {"type": "object"},
        },
        {
            "name": "create_context_memory",
            "description": "Write back.",
            "inputSchema": {"type": "object"},
        },
    ]
}


async def test_list_tools_drops_every_write_tool() -> None:
    client = CatalogClient("http://om", "jwt", client=_mock(lambda r: _rpc_ok(TOOLS_PAYLOAD)))

    names = {spec.name for spec in await client.list_tools()}

    assert names == {"search_metadata", "get_asset_context"}
    assert names <= READ_ONLY_TOOLS


async def test_write_tools_are_not_in_the_allowlist() -> None:
    # A regression guard on the allowlist itself, not on any one call: §3.4's
    # write-back is M4 and gated on human review.
    for name in ("patch_entity", "create_glossary", "create_tag", "create_context_memory"):
        assert name not in READ_ONLY_TOOLS


async def test_call_tool_refuses_a_name_outside_the_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover — must not run
        raise AssertionError("the request should never leave hub-api")

    client = CatalogClient("http://om", "jwt", client=_mock(handler))

    with pytest.raises(CatalogError, match="not a tool this agent may call"):
        await client.call_tool("patch_entity", {"fqn": "x"})


async def test_call_tool_prefers_structured_content() -> None:
    result = {
        "content": [{"type": "text", "text": "pretty printed"}],
        "structuredContent": {"total": 67},
    }
    client = CatalogClient("http://om", "jwt", client=_mock(lambda r: _rpc_ok(result)))

    assert json.loads(await client.call_tool("search_metadata", {"query": "health"})) == {
        "total": 67
    }


async def test_tool_error_comes_back_as_text_not_an_exception() -> None:
    # The live shape when semantic search is not configured: HTTP 200, isError.
    result = {
        "content": [{"type": "text", "text": '{"error":"Semantic search is not enabled."}'}],
        "isError": True,
    }
    client = CatalogClient("http://om", "jwt", client=_mock(lambda r: _rpc_ok(result)))

    # The loop hands this to the model as a tool result so it can route around it.
    assert "Semantic search is not enabled" in await client.call_tool(
        "semantic_search", {"query": "x"}
    )


async def test_persona_preamble_returns_empty_on_a_tool_error() -> None:
    # The live shape today: no persona is configured, so this must not end up
    # pasted into the system prompt as if it were curated context.
    result = {
        "content": [{"type": "text", "text": '{"error":"No active persona is configured"}'}],
        "isError": True,
    }
    client = CatalogClient("http://om", "jwt", client=_mock(lambda r: _rpc_ok(result)))

    assert await client.persona_preamble("healthcare-researcher") == ""


async def test_sse_framed_response_is_decoded() -> None:
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
    client = CatalogClient(
        "http://om",
        "jwt",
        client=_mock(
            lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        ),
    )

    assert await client.list_tools() == ()


async def test_jsonrpc_error_raises() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    client = CatalogClient(
        "http://om", "jwt", client=_mock(lambda r: httpx.Response(200, json=payload))
    )

    with pytest.raises(CatalogError, match="Method not found"):
        await client.list_tools()


async def test_bearer_token_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        return _rpc_ok({"tools": []})

    await CatalogClient("http://om", "s3cret", client=_mock(handler)).list_tools()

    assert seen["auth"] == "Bearer s3cret"
