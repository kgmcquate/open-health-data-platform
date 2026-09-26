"""hub_api.models — layering explicit models.yaml overrides on top of bulk
auto-discovered OpenAI-spec models, without hitting a real model API
(build_agent's Agent construction makes no network call — see
ohdp_agent.loop.build_agent).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.toolsets import FunctionToolset

from hub_api import models as hub_models
from hub_api.models import (
    ModelOverride,
    ModelsConfig,
    SearchSummaryModel,
    build_agents,
    build_search_summary_model,
)


async def _noop() -> str:
    return "ok"


def test_override_customizes_an_auto_discovered_model() -> None:
    openai_models = {"openai/gpt-4o-mini": ("https://openrouter.ai/api/v1", "sk-or-1")}
    weather = FunctionToolset([_noop])
    overrides = ModelsConfig(
        models=[ModelOverride(id="openai/gpt-4o-mini", label="Fast triage", tools=["weather"])]
    )

    agents = build_agents(openai_models, {"weather": weather}, overrides)

    config = agents["openai/gpt-4o-mini"]
    assert config.label == "Fast triage"
    # Overriding does not lose auto-discovery's own base_url/api_key — the
    # override only customizes label/system_prompt/tools when it gives no
    # base_url of its own.
    base_url = config.agent.model.provider.base_url  # type: ignore[union-attr]
    assert base_url.startswith("https://openrouter.ai")


def test_override_with_its_own_backend_registers_independently() -> None:
    overrides = ModelsConfig(
        models=[
            ModelOverride(
                id="on-prem-llama", label="On-prem Llama", base_url="http://localhost:11434/v1"
            )
        ]
    )

    agents = build_agents({}, {}, overrides)

    assert "on-prem-llama" in agents
    assert agents["on-prem-llama"].label == "On-prem Llama"


def test_unresolvable_override_is_skipped_not_fatal() -> None:
    # No base_url given, and this id is not in openai_models — nothing tells
    # us what backend to use.
    overrides = ModelsConfig(models=[ModelOverride(id="mystery-model")])

    agents = build_agents({}, {}, overrides)

    assert agents == {}


def test_unknown_tool_id_is_dropped_not_fatal() -> None:
    openai_models = {"openai/gpt-4o-mini": ("https://openrouter.ai/api/v1", "sk-or-1")}
    overrides = ModelsConfig(
        models=[ModelOverride(id="openai/gpt-4o-mini", tools=["does-not-exist"])]
    )

    agents = build_agents(openai_models, {}, overrides)

    assert "openai/gpt-4o-mini" in agents


def test_load_models_config_missing_file_is_empty() -> None:
    assert hub_models.load_models_config(Path("/does/not/exist.yaml")).models == []


def test_default_provider_names_vendor_and_router() -> None:
    assert (
        hub_models.default_provider("z-ai/glm-5.3-flash", "https://openrouter.ai/api/v1")
        == "Z.ai via OpenRouter"
    )
    # An unlisted vendor prefix is shown as-is, not dropped.
    assert (
        hub_models.default_provider("acme/model-1", "https://openrouter.ai/api/v1")
        == "acme via OpenRouter"
    )
    # No vendor prefix and no known router: fall back to the backend host.
    assert hub_models.default_provider("llama3", "http://localhost:11434/v1") == "localhost"


def test_explicit_provider_overrides_the_default() -> None:
    agents = build_agents(
        {"z-ai/glm-5.3-flash": ("https://openrouter.ai/api/v1", "k")},
        {},
        ModelsConfig(models=[ModelOverride(id="z-ai/glm-5.3-flash", provider="Zhipu AI")]),
    )
    assert agents["z-ai/glm-5.3-flash"].provider == "Zhipu AI"


def test_search_summary_model_resolves_through_auto_discovery() -> None:
    openai_models = {"z-ai/glm-5.3-flash": ("https://openrouter.ai/api/v1", "sk-or-1")}
    config = ModelsConfig(
        search_summary_model=SearchSummaryModel(id="z-ai/glm-5.3-flash", system_prompt="Summarize.")
    )

    model = build_search_summary_model(openai_models, config)

    assert model is not None
    assert model.model.model_name == "z-ai/glm-5.3-flash"
    assert model.system_prompt == "Summarize."


def test_search_summary_model_absent_or_unresolvable_is_none() -> None:
    assert build_search_summary_model({}, ModelsConfig()) is None
    config = ModelsConfig(
        search_summary_model=SearchSummaryModel(id="nobody/knows", system_prompt="Summarize.")
    )
    assert build_search_summary_model({}, config) is None


def test_load_models_config_reads_the_search_summary_section(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(
        "models: []\nsearch_summary_model:\n  id: z-ai/glm-5.3-flash\n  system_prompt: Summarize.\n"
    )

    config = hub_models.load_models_config(path)

    assert config.search_summary_model == SearchSummaryModel(
        id="z-ai/glm-5.3-flash", system_prompt="Summarize."
    )


def test_search_summary_section_without_a_prompt_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("search_summary_model:\n  id: z-ai/glm-5.3-flash\n")

    assert hub_models.load_models_config(path).search_summary_model is None
