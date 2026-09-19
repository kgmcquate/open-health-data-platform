from ohdp_shared.settings import Settings


def test_openai_backends_pairs_urls_with_keys_by_index() -> None:
    settings = Settings(
        openai_api_base_urls="https://openrouter.ai/api/v1;https://api.openai.com/v1",
        openai_api_keys="sk-or-1;sk-oai-1",
    )
    assert settings.openai_backends == [
        ("https://openrouter.ai/api/v1", "sk-or-1"),
        ("https://api.openai.com/v1", "sk-oai-1"),
    ]


def test_openai_backends_allows_a_keyless_backend() -> None:
    """A local server (vLLM, Ollama) often needs no key at all."""
    settings = Settings(
        openai_api_base_urls="https://openrouter.ai/api/v1;http://localhost:11434/v1",
        openai_api_keys="sk-or-1",
    )
    assert settings.openai_backends == [
        ("https://openrouter.ai/api/v1", "sk-or-1"),
        ("http://localhost:11434/v1", ""),
    ]


def test_openai_backends_empty_by_default() -> None:
    # `_env_file=None`: the field default is what is under test, not whatever
    # this developer's own local .env happens to have configured for real use.
    assert Settings(_env_file=None).openai_backends == []
