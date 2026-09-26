"""Discovering what models this deployment can answer with, and building the
pydantic-ai `Agent` for each one (§4a).

Two layers, in the order they are applied:

  1. Bulk auto-discovery: every model reported by `GET /models` on each
     backend in `settings.openai_backends` (`OHDP_OPENAI_API_BASE_URLS`/
     `_KEYS`) — the same call the hub chat makes to build its model picker, so
     the list an operator gets is exactly what their key can see, never a
     hand-maintained duplicate of it that can drift.
  2. Explicit overrides (`apps/api/config/models.yaml`): a model id already
     covered by (1) gets its label/system prompt/extra tools customized; an id
     that is not gets registered as its own model, for a backend not worth
     bulk-discovering.

An `Agent` is built once per model here, at startup, and reused for every
request that model answers (`ohdp_agent.loop.Deps` is what carries
per-request state into an otherwise-stateless, shared `Agent`) — building 400+
of them is cheap; it is pure object construction, no network calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import openai
import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent
from pydantic_ai.toolsets import AbstractToolset

from hub_api import config_dir
from ohdp_agent.loop import (
    ASK_USER_TOOLSET,
    CUBE_TOOLSET,
    LITERATURE_TOOLSET,
    SYSTEM_PROMPT,
    Deps,
    build_agent,
)
from ohdp_shared import env_file_values, get_logger, settings

# The in-process tool groups a model can name in its `tools:` list, alongside
# any id `tools.yaml` declares — resolution is entirely this module's, driven
# by models.yaml; ohdp_agent.loop has no registry of its own and nothing here
# is attached to a model that does not name it.
_IN_PROCESS_TOOLSETS: dict[str, AbstractToolset[Deps]] = {
    "cube": CUBE_TOOLSET,
    "literature": LITERATURE_TOOLSET,
    "ask-user": ASK_USER_TOOLSET,
}
# The one entry in a model's `tools:` list that is not in `_IN_PROCESS_TOOLSETS`:
# OpenMetadata's advertised tools can change between deploys, so this one is
# discovered fresh per request (`ohdp_agent.loop._catalog_toolset`) rather than
# resolved once here like the dict above and `tools.yaml`'s connections.
_CATALOG_TOOLS_ID = "catalog"

log = get_logger(__name__)

CONFIG_PATH = config_dir() / "models.yaml"


@dataclass
class ModelConfig:
    agent: Agent[Deps, str]
    label: str
    # Who serves this model, shown under its label in the picker — e.g.
    # "Z.ai via OpenRouter". models.yaml's `provider:` if given, otherwise
    # `default_provider`'s guess from the id and backend URL.
    provider: str = ""
    system_prompt: str = SYSTEM_PROMPT
    # True only for an id explicitly listed in models.yaml. The chat page's
    # model picker (hub_api.chat.models) shows only these — auto-discovered
    # entries stay usable (an existing thread or a direct API call can still
    # name them) but stop cluttering the picker with every model a configured
    # OpenAI-spec key happens to see.
    configured: bool = False
    # True only when this model's `tools:` list names "catalog" — the one tool
    # group that cannot be resolved to a static toolset at agent-construction
    # time (see `_CATALOG_TOOLS_ID` above), so `hub_api.chat` reads this and
    # passes it to `ohdp_agent.loop.run`'s `include_catalog_tools` instead.
    # Every other tool group ("cube", "literature", tools.yaml's connections)
    # is resolved once below and baked into `agent`'s own toolsets directly.
    include_catalog_tools: bool = False


# model id -> its reusable Agent plus how it should be presented and prompted.
AgentRegistry = dict[str, ModelConfig]


class ModelOverride(BaseModel):
    id: str
    label: str = ""
    # Empty means `default_provider`'s guess — set it when that guess is wrong
    # or too terse for the picker.
    provider: str = ""
    # Empty means: reuse whatever (1) or (2) above already resolved for this
    # id — the override only customizes label/system_prompt/tools. Given,
    # this id is registered fresh, independent of auto-discovery.
    base_url: str = ""
    # The *name* of a variable in .env (ohdp_shared.env_file_values), not a
    # `${...}` template — there being exactly one value to resolve here, not
    # one embedded in a larger string the way tools.yaml's headers can be.
    api_key_env: str = ""
    # Empty means the built-in SYSTEM_PROMPT — see models.yaml's own comment
    # for why a non-empty value replaces it outright rather than extending it.
    system_prompt: str = ""
    # This model's entire tool surface — nothing is attached by default. Each
    # entry is either a `BUILTIN_TOOLSETS` key ("cube", "literature"), the
    # special "catalog" id (`_CATALOG_TOOLS_ID`), or a connection id from
    # tools.yaml. See models.yaml's own comment for the operator side of this.
    tools: list[str] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    models: list[ModelOverride] = Field(default_factory=list)


# Model-id prefixes (an OpenRouter-style "vendor/model" id) -> the vendor's
# display name. An unlisted prefix is shown as-is rather than guessed at.
_VENDOR_NAMES = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "google": "Google",
    "meta-llama": "Meta",
    "mistralai": "Mistral AI",
    "moonshotai": "Moonshot AI",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "x-ai": "xAI",
    "z-ai": "Z.ai",
}

# Backend hosts that route to other vendors' models -> their display name.
_ROUTER_NAMES = {"openrouter.ai": "OpenRouter"}


def default_provider(model_id: str, base_url: str) -> str:
    """E.g. "Z.ai via OpenRouter": the vendor named by the id's prefix and the
    router the backend URL points at, whichever of the two is known, or else
    the backend's host."""
    host = urlparse(base_url).hostname or ""
    router = _ROUTER_NAMES.get(host.removeprefix("api."), "")
    prefix = model_id.split("/", 1)[0] if "/" in model_id else ""
    vendor = _VENDOR_NAMES.get(prefix, prefix)
    if vendor and router:
        return f"{vendor} via {router}"
    return vendor or router or host


def load_models_config(path: Path = CONFIG_PATH) -> ModelsConfig:
    """Skips a model entry that fails to parse rather than raising — same
    reasoning as `hub_api.tool_connections.load_config`: one operator typo
    must cost that model, not every other configured model, and must not
    take hub-api's startup down with it.
    """
    if not path.exists():
        return ModelsConfig()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        log.error("models_config_invalid", path=str(path), error=str(exc))
        return ModelsConfig()

    overrides: list[ModelOverride] = []
    for item in raw.get("models", []) or []:
        try:
            overrides.append(ModelOverride.model_validate(item))
        except ValidationError as exc:
            log.error(
                "model_override_invalid",
                id=item.get("id") if isinstance(item, dict) else None,
                error=str(exc),
            )
    return ModelsConfig(models=overrides)


async def discover_openai_models() -> dict[str, tuple[str, str]]:
    """model id -> (base_url, api_key) for every model every configured
    backend reports. Best-effort: a backend that is down or misconfigured is
    logged and skipped, never fatal — the rest of the hub must still start
    (main.py's lifespan already treats the database the same way).
    """
    registry: dict[str, tuple[str, str]] = {}
    for base_url, api_key in settings.openai_backends:
        try:
            # The openai SDK refuses to construct a client with an empty
            # api_key at all — even for a local server that ignores auth
            # entirely, so a keyless backend (settings.py explicitly allows
            # one) needs *something* non-empty here.
            client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "not-required")
            page = await client.models.list()
        except openai.OpenAIError as exc:
            log.error("openai_backend_unavailable", base_url=base_url, error=str(exc))
            continue
        for model in page.data:
            if model.id in registry:
                log.warning("openai_model_id_collision", model=model.id, base_url=base_url)
                continue
            registry[model.id] = (base_url, api_key)
        log.info("openai_backend_models", base_url=base_url, count=len(page.data))
    return registry


def build_agents(
    openai_models: dict[str, tuple[str, str]],
    tool_connections: dict[str, AbstractToolset[Deps]],
    overrides: ModelsConfig | None = None,
) -> AgentRegistry:
    agents: AgentRegistry = {}
    for model_id, (base_url, api_key) in openai_models.items():
        agents[model_id] = ModelConfig(
            agent=build_agent(model_id=model_id, base_url=base_url, api_key=api_key),
            label=model_id,
            provider=default_provider(model_id, base_url),
        )

    for override in (overrides or load_models_config()).models:
        base_url, api_key = override.base_url, ""
        if base_url:
            api_key = (
                env_file_values().get(override.api_key_env, "") if override.api_key_env else ""
            )
        elif override.id in openai_models:
            base_url, api_key = openai_models[override.id]
        else:
            log.warning("model_override_unresolvable", id=override.id)
            continue

        # "catalog" cannot be resolved to a static toolset here (see
        # `_CATALOG_TOOLS_ID`'s comment) — pulled out before resolving the rest
        # against `_IN_PROCESS_TOOLSETS` + tools.yaml so it does not show up as
        # an unknown tool below.
        include_catalog_tools = _CATALOG_TOOLS_ID in override.tools
        static_toolsets: dict[str, AbstractToolset[Deps]] = {
            **_IN_PROCESS_TOOLSETS,
            **tool_connections,
        }
        requested = [t for t in override.tools if t != _CATALOG_TOOLS_ID]
        resolved_ids = [t for t in requested if t in static_toolsets]
        extra_toolsets = [static_toolsets[t] for t in resolved_ids]
        missing = set(requested) - set(resolved_ids)
        if missing:
            log.warning("model_override_unknown_tools", id=override.id, missing=sorted(missing))

        agents[override.id] = ModelConfig(
            agent=build_agent(
                model_id=override.id,
                base_url=base_url,
                api_key=api_key,
                extra_toolsets=extra_toolsets,
            ),
            label=override.label or override.id,
            provider=override.provider or default_provider(override.id, base_url),
            system_prompt=override.system_prompt or SYSTEM_PROMPT,
            configured=True,
            include_catalog_tools=include_catalog_tools,
        )

    return agents
