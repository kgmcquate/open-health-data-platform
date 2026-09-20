"""Config-driven MCP and OpenAPI tool connections (`apps/api/config/tools.yaml`,
docs/chatbot.md §4a) — for anything beyond the platform's own in-process
cube/literature/catalog tools (`ohdp_agent.loop.CUBE_TOOLSET`/`LITERATURE_TOOLSET`,
plus `_catalog_toolset`), which a model gets by naming "cube"/"literature"/"catalog"
in models.yaml's `tools:` the same way it names a connection from this file
(`hub_api.models` resolves both). Nothing is attached to a model by default;
see models.yaml's own comment.

An MCP connection is a URL pydantic-ai's own `MCPToolset` already knows how to
speak to. An OpenAPI connection has no such client built in, but FastMCP does
(`FastMCP.from_openapi` — already a dependency here): fetch the spec once at
and hand that server to the same `MCPToolset` — no second toolset
implementation needed for the two connection types this file supports.

`build_local_openapi_toolset`, below, is a third path for one specific case:
hub-api's own `/tools` app (`hub_api.issues.tools_app`). That used to be just
another `tools.yaml` OpenAPI connection (id `ohdp-tools`), fetched over a real
HTTP call to hub-api's own cluster address. That call cannot succeed —
`lifespan` (`hub_api.main`) runs it before hub-api is serving any requests at
all, including its own, and the `Recreate` deploy strategy means there is
never a second pod up to answer it either. It failed on every single startup,
silently (this file's own best-effort-per-connection contract), which meant
`render_dashboard` and `report_issue` were never actually attached to any
model. Reading the spec off the app object and dispatching over an ASGI
transport instead of a socket sidesteps the whole problem: there is no
listener to wait for, because the call never leaves the process.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import httpx2
import yaml
from fastapi import FastAPI
from fastmcp import FastMCP
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from hub_api import config_dir
from ohdp_agent.loop import Deps
from ohdp_shared import env_file_values, get_logger

log = get_logger(__name__)

CONFIG_PATH = config_dir() / "tools.yaml"

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
_connection_adapter: TypeAdapter[Connection] = TypeAdapter(Connection)


class ToolsConfig(BaseModel):
    connections: list[Connection] = Field(default_factory=list)


def load_config(path: Path = CONFIG_PATH) -> ToolsConfig:
    """Skips a connection that fails to parse rather than raising — this file
    is hand-edited by an operator, and a typo in *one* entry (the wrong field
    for its `type`, most commonly) must cost that connection, not silently
    drop every other one in the file, and must not take hub-api's startup
    down with it. Broken YAML syntax (the whole file, not one entry) still
    empties the config — there is no per-entry unit left to salvage from that.
    """
    if not path.exists():
        return ToolsConfig()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        log.error("tools_config_invalid", path=str(path), error=str(exc))
        return ToolsConfig()

    connections: list[Connection] = []
    for item in raw.get("connections", []) or []:
        try:
            connections.append(_connection_adapter.validate_python(item))
        except ValidationError as exc:
            log.error(
                "tool_connection_invalid",
                id=item.get("id") if isinstance(item, dict) else None,
                error=str(exc),
            )
    return ToolsConfig(connections=connections)


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


def build_local_openapi_toolset(
    dispatch_app: FastAPI,
    spec_app: FastAPI,
    *,
    id: str,
    mount_path: str,
    headers: dict[str, str],
) -> AbstractToolset[Deps]:
    """Build an OpenAPI toolset from a sub-app already mounted on `dispatch_app`
    in this process, instead of `_build_openapi_toolset`'s network fetch — see
    this module's own docstring for why `ohdp-tools` needs this instead of an
    ordinary `tools.yaml` connection.

    `spec_app.openapi()` reads the narrow spec straight off the sub-app's own
    routes — no request involved, and no risk of handing the model every route
    on `dispatch_app` (`hub_api.main`'s own comment on why the sub-app exists
    at all). But calls still have to go *through* `dispatch_app`, not
    `spec_app` directly: `spec_app` (`hub_api.issues.tools_app`) carries no
    middleware of its own, and its routes read `request.session`
    (`hub_api.issues.get_reporter`) relying on the `SessionMiddleware`
    `dispatch_app` installs — reachable because a mounted sub-app shares its
    parent's middleware scope for any request that actually arrives through
    the parent. Dispatching against `spec_app` in isolation would 500 on that
    same `request.session` access. `mount_path` is where `dispatch_app` mounts
    `spec_app` (e.g. `"/tools"`), prepended to `spec_app`'s own root-relative
    paths so a call for `/render_dashboard` in the spec actually lands on
    `/tools/render_dashboard` on `dispatch_app`.
    """
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=dispatch_app),
        base_url=f"http://{id}{mount_path}",
        headers=headers,
    )
    server = FastMCP.from_openapi(spec_app.openapi(), client=client, name=id)
    return MCPToolset(client=server, id=id)


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
