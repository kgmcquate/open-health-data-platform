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

import openai
import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent
from pydantic_ai.toolsets import AbstractToolset

from ohdp_agent.loop import SYSTEM_PROMPT, Deps, build_agent
from ohdp_shared import env_file_values, get_logger, settings

log = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@dataclass
class ModelConfig:
    agent: Agent[Deps, str]
    label: str
    system_prompt: str = SYSTEM_PROMPT
    # True only for an id explicitly listed in models.yaml. The chat page's
    # model picker (hub_api.chat.models) shows only these — auto-discovered
    # entries stay usable (an existing thread or a direct API call can still
    # name them) but stop cluttering the picker with every model a configured
    # OpenAI-spec key happens to see.
    configured: bool = False


# model id -> its reusable Agent plus how it should be presented and prompted.
AgentRegistry = dict[str, ModelConfig]


class ModelOverride(BaseModel):
    id: str
    label: str = ""
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
    # ids from tools.yaml's connections. Always additive to BUILTIN_TOOLSET,
    # never a replacement for it — see ohdp_agent.loop.build_agent.
    tools: list[str] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    models: list[ModelOverride] = Field(default_factory=list)


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

        extra_toolsets = [tool_connections[t] for t in override.tools if t in tool_connections]
        missing = set(override.tools) - set(tool_connections)
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
            system_prompt=override.system_prompt or SYSTEM_PROMPT,
            configured=True,
        )

    return agents
