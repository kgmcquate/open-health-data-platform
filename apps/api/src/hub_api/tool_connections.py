"""Config-driven MCP and OpenAPI tool connections (`apps/api/config/tools.yaml`,
docs/chatbot.md §4a) — for anything beyond the platform's own built-in
cube/literature/catalog tools (`ohdp_agent.loop.BUILTIN_TOOLSET`), which every
model keeps regardless of what is configured here.

An MCP connection is a URL pydantic-ai's own `MCPToolset` already knows how to
speak to. An OpenAPI connection has no such client built in, but FastMCP does
(`FastMCP.from_openapi` — already a dependency here, `ohdp_mcp` is built on
it): fetch the spec once at startup, turn it into an in-process MCP server,
and hand that server to the same `MCPToolset` — no second toolset
implementation needed for the two connection types this file supports.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import httpx2
import yaml
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from ohdp_agent.loop import Deps
from ohdp_shared import env_file_values, get_logger

log = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "tools.yaml"

_ENV_REF = re.compile(r"\$\{(\w+)\}")


def _resolve(value: str) -> str:
    """Replace every `${NAME}` in `value` with `.env`'s NAME, or "" if unset —
    a placeholder that never resolves is a misconfigured connection, not a
    reason to crash startup for every other one.
    """

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = env_file_values().get(name, "")
        if not resolved:
            log.warning("tool_connection_env_ref_unresolved", name=name)
        return resolved

    return _ENV_REF.sub(repl, value)


class McpConnection(BaseModel):
    id: str
    type: Literal["mcp"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class OpenApiConnection(BaseModel):
    id: str
    type: Literal["openapi"]
    spec_url: str
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)


Connection = Annotated[McpConnection | OpenApiConnection, Field(discriminator="type")]


class ToolsConfig(BaseModel):
    connections: list[Connection] = Field(default_factory=list)


def load_config(path: Path = CONFIG_PATH) -> ToolsConfig:
    if not path.exists():
        return ToolsConfig()
    raw = yaml.safe_load(path.read_text()) or {}
    return ToolsConfig.model_validate(raw)


def _build_mcp_toolset(conn: McpConnection) -> AbstractToolset[Deps]:
    headers = {k: _resolve(v) for k, v in conn.headers.items()}
    return MCPToolset(client=conn.url, headers=headers, id=conn.id)


async def _build_openapi_toolset(conn: OpenApiConnection) -> AbstractToolset[Deps]:
    headers = {k: _resolve(v) for k, v in conn.headers.items()}
    async with httpx2.AsyncClient(headers=headers) as fetcher:
        response = await fetcher.get(conn.spec_url)
        response.raise_for_status()
        spec = yaml.safe_load(response.text)
    client = httpx2.AsyncClient(base_url=conn.base_url, headers=headers)
    server = FastMCP.from_openapi(spec, client=client, name=conn.id)
    return MCPToolset(client=server, id=conn.id)


async def load_tool_connections(
    config: ToolsConfig | None = None,
) -> dict[str, AbstractToolset[Deps]]:
    """id -> toolset, for every connection `tools.yaml` declares.

    Best-effort, like `hub_api.models.discover_openai_models`: a connection
    that cannot be built — a spec URL that 404s, an unresolved `${NAME}` — is
    logged and skipped, not fatal. Every other model must still start.
    """
    toolsets: dict[str, AbstractToolset[Deps]] = {}
    for conn in (config or load_config()).connections:
        try:
            if conn.type == "mcp":
                toolsets[conn.id] = _build_mcp_toolset(conn)
            else:
                toolsets[conn.id] = await _build_openapi_toolset(conn)
        except Exception as exc:  # noqa: BLE001 — one bad connection must not stop the rest
            log.error("tool_connection_unavailable", id=conn.id, type=conn.type, error=str(exc))
    return toolsets
