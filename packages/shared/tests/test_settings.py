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


def test_admin_emails_list_is_normalized() -> None:
    """Whitespace and casing are a formatting accident, not a permission
    boundary — `hub_api.auth.is_admin` compares against this lowercased."""
    settings = Settings(admin_emails=" Someone@Example.org ,other@example.org")
    assert settings.admin_emails_list == ["someone@example.org", "other@example.org"]


def test_nobody_is_an_admin_by_default() -> None:
    """A deployment that was never told who its admins are has none, rather
    than falling back to anyone who can sign in. `_env_file=None` so this tests
    the field default and not the developer's own .env."""
    assert Settings(_env_file=None).admin_emails_list == []
