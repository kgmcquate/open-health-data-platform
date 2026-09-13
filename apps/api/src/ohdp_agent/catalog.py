"""Context and discovery over OpenMetadata's MCP server (docs/chatbot.md §2.1, §3).

This is the layer that makes the bot's domain knowledge editable without a
deploy: personas, glossary terms and Context Center articles live in the
catalog, and the agent reads them at runtime.

**Verified against the live deployment (M3.0).** OpenMetadata 2.0.1 advertises
`openmetadata-mcp-stateless/1.1.0` with 24 tools, and every Context Center tool
§2.1 hoped for is present in OSS — nothing is Collate-gated, so §10.1 resolves
in favour of §3.1 as written. Two of them are nonetheless inert on this
deployment, which is a configuration gap rather than a licence one:

  - `find_context` and `semantic_search` both answer "Semantic search is not
    enabled. Configure vector embeddings in the OpenMetadata server settings."
    Until that is configured, the plan phase discovers assets through
    `search_metadata` (keyword, and working — 67 assets indexed) instead of
    §3.2's `find_context`.
  - `get_persona_context` 404s with "No active persona is configured for this
    user". Personas are M3.3; until they are seeded and one is bound to the
    `managementbot` user, `persona_preamble` returns "" and the loop falls back
    to its built-in system prompt.

Neither is fatal and neither is worked around here. The tools stay advertised to
the model, their errors come back as tool results, and the agent routes around
them — which is also what happens on the day somebody enables embeddings and
they start working, with no deploy on our side.

Two consequences of "stateless" shape this client:

  - **No session handshake.** The server takes a bare `tools/call` POST with no
    `initialize` and no `Mcp-Session-Id`, so this is ~100 lines of JSON-RPC
    rather than a dependency on the `mcp` SDK and its transport machinery. If a
    future OM release becomes stateful, `_rpc` is the only thing that changes.
  - **The client is ours**, which is the point ADR-0016 makes about the Cube
    layer too: tool calls stay inside hub-api's logging and quota path rather
    than being made by Anthropic's servers through the API's MCP connector.

Eleven of those 24 tools write to the catalog (`patch_entity`,
`create_glossary`, `create_tag`, `create_lineage`, ...). They are excluded by an
**allowlist**, not by omission from the prompt: the agent's tool surface is the
safety control, and a model that hallucinates `patch_entity` should get an error
from us rather than an edit to the catalog. §3.4's reviewed write-back is M4 and
will add exactly one name to that list, deliberately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)

# Read-only tools the agent may call. Everything else OM advertises — every
# `create_*`, `patch_entity`, `create_lineage`, `create_context_memory` — is
# refused here even if the model asks for it by name.
#
# `create_context_memory` is the write-back loop of §3.4. It stays off until
# there is a human review step in front of it: catalog content lands in the
# prompt (§3.2), so an unreviewed write-back is a prompt-injection cycle the
# agent can feed itself.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "get_persona_context",
        "get_user_context",
        "find_context",
        "search_company_context",
        "get_company_context",
        "get_knowledge_content",
        "search_metadata",
        "semantic_search",
        "get_entity_details",
        "get_asset_context",
        "get_entity_lineage",
    }
)

# Tool output is human-authored catalog text and must be treated as data, never
# as instructions (docs/chatbot.md §6). Truncating also keeps one verbose
# `get_asset_context` from crowding out the rest of the plan phase.
MAX_TOOL_RESULT_CHARS = 20_000


class CatalogError(RuntimeError):
    """OpenMetadata's MCP server was unreachable or refused the call."""


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool. Field names match `anthropic.types.ToolParam`'s."""

    name: str
    description: str
    input_schema: dict[str, Any]


class CatalogClient:
    """JSON-RPC client for OpenMetadata's stateless MCP endpoint."""

    def __init__(
        self,
        base_url: str,
        jwt: str,
        *,
        timeout: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/mcp"
        self._jwt = jwt
        self._timeout = timeout
        self._client = client
        self._tools: tuple[ToolSpec, ...] | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._jwt}",
            "Content-Type": "application/json",
            # The server may answer either way; it picks JSON when both are
            # offered, and `_result` handles the SSE framing if it does not.
            "Accept": "application/json, text/event-stream",
        }

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        if self._client is not None:
            response = await self._client.post(self._url, headers=self._headers, json=body)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, headers=self._headers, json=body)

        if response.status_code >= 400:
            log.warning("om_mcp_failed", method=method, status=response.status_code)
            raise CatalogError(f"OpenMetadata MCP returned {response.status_code}")
        return _result(response.text, method)

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        """The allowlisted subset of what this deployment actually advertises.

        Discovered at runtime rather than hard-coded: OM ships new tools between
        minor versions, and a schema copied into our source drifts silently. The
        allowlist above still bounds what comes back, so discovery can only ever
        narrow the surface, never widen it.
        """
        if self._tools is not None:
            return self._tools

        payload = await self._rpc("tools/list", {})
        specs: list[ToolSpec] = []
        for raw in payload.get("tools", []):
            name = str(raw.get("name", ""))
            if name not in READ_ONLY_TOOLS:
                continue
            schema = raw.get("inputSchema")
            specs.append(
                ToolSpec(
                    name=name,
                    description=str(raw.get("description", "")),
                    input_schema=schema if isinstance(schema, dict) else {"type": "object"},
                )
            )

        missing = READ_ONLY_TOOLS - {s.name for s in specs}
        if missing:
            # Not fatal: the loop degrades to whatever context tools do exist.
            # Worth a log line, because it is the signal that an OM upgrade
            # moved something behind the Collate licence.
            log.warning("om_mcp_tools_missing", missing=sorted(missing))

        self._tools = tuple(specs)
        log.info("om_mcp_tools", count=len(self._tools))
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call one allowlisted tool and flatten its content to text.

        Raises rather than returning an error string for a non-allowlisted name:
        the caller turns that into a tool_result the model can see and recover
        from, and the attempt is worth a log line either way.
        """
        if name not in READ_ONLY_TOOLS:
            log.warning("om_mcp_tool_refused", tool=name)
            raise CatalogError(f"{name!r} is not a tool this agent may call")

        text, _ = await self._call(name, arguments)
        return text

    async def _call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """`call_tool` plus the `isError` flag, for callers that must branch on it.

        A failed MCP tool call is an HTTP 200 carrying `isError: true` and a
        message in the content block — not an HTTP error. Returning the message
        as the tool result is right for the loop (the model reads it and picks
        another tool), and wrong for `persona_preamble`, which must not paste it
        into the system prompt.
        """
        payload = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = _content_text(payload)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "\n\n[truncated]"
        failed = bool(payload.get("isError"))
        log.info("om_mcp_tool_called", tool=name, chars=len(text), failed=failed)
        return text, failed

    async def persona_preamble(self, persona: str) -> str:
        """The curated context document for `persona` (§3.1), or "" if absent.

        Never raises, and never returns an error body as if it were context: a
        missing persona degrades to the built-in system prompt rather than
        failing the user's question or feeding "No active persona is configured"
        into the model as though it were curated guidance. Personas are seeded in
        `catalog/openmetadata/seed/` (M3.3) and edited in OM by a human after.

        The argument is `personaName` — OM's own schema calls it that, and it
        wants a persona FQN, not a display name.
        """
        try:
            text, failed = await self._call("get_persona_context", {"personaName": persona})
        except CatalogError as exc:
            log.warning("om_persona_context_unavailable", persona=persona, error=str(exc))
            return ""
        if failed:
            log.warning("om_persona_context_unavailable", persona=persona, error=text[:200])
            return ""
        return text


def _result(text: str, method: str) -> dict[str, Any]:
    """Unwrap a JSON-RPC result from either a plain body or an SSE frame."""
    payload = _decode(text)
    if "error" in payload:
        detail = payload["error"]
        message = detail.get("message", detail) if isinstance(detail, dict) else detail
        raise CatalogError(f"OpenMetadata MCP {method} failed: {str(message)[:300]}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CatalogError(f"OpenMetadata MCP {method} returned no result")
    return result


def _decode(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("event:") or body.startswith("data:"):
        # SSE framing: one or more `data:` lines carry the JSON payload.
        body = "".join(
            line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")
        )
    try:
        payload: dict[str, Any] = json.loads(body)
    except ValueError as exc:
        raise CatalogError("OpenMetadata MCP returned a non-JSON body") from exc
    return payload


def _content_text(result: dict[str, Any]) -> str:
    """MCP content blocks → the text the model sees.

    `structuredContent` is preferred when present: OM returns the same data in
    both, and the JSON form survives the model's reading better than the
    pretty-printed text block does.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return json.dumps(structured, separators=(",", ":"))

    parts: list[str] = []
    for block in result.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)
