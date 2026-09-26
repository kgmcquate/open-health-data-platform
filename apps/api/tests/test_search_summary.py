"""`GET /api/search/summary` (hub_api.search_summary) — the search page's AI
overview. The model is pydantic-ai's `TestModel`; what is under test is the
gating around it: sign-in, the token gate, the cache and the rate limit."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from sqlalchemy.engine import Engine

from hub_api import chat, db, search_summary
from hub_api.main import app
from hub_api.models import SearchSummaryConfig
from ohdp_shared import settings

USER = "researcher@example.org"
OUTPUT = {
    "summary": "The platform has influenza ED visit data.",
    "prompts": ["How have flu ED visits trended this season?", "Which states lead?", "And RSV?"],
}


@pytest.fixture
def model() -> TestModel:
    return TestModel(custom_output_args=OUTPUT)


@pytest.fixture
def client(tmp_path: Path, model: TestModel) -> Iterator[TestClient]:
    engine: Engine = db.make_engine(f"sqlite:///{tmp_path}/summary.db")
    db.ensure_schema(engine)
    app.state.engine = engine
    app.dependency_overrides[chat.get_engine] = lambda: engine
    app.dependency_overrides[chat.get_user_email] = lambda: USER
    app.dependency_overrides[chat.get_user_tier] = lambda: "free"
    app.state.search_summary = SearchSummaryConfig(model=model, system_prompt="Summarize.")
    search_summary._cache.clear()
    search_summary._recent.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.state.engine = None
        app.state.search_summary = None


def test_returns_a_summary_and_three_prompts(client: TestClient) -> None:
    response = client.get("/api/search/summary", params={"q": "influenza"})

    assert response.status_code == 200
    assert response.json() == OUTPUT


def test_signed_out_is_refused(client: TestClient) -> None:
    del app.dependency_overrides[chat.get_user_email]

    response = client.get("/api/search/summary", params={"q": "influenza"})

    assert response.status_code == 401


def test_the_same_query_is_answered_from_the_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "search_summaries_per_hour", 1)

    first = client.get("/api/search/summary", params={"q": "Influenza"})
    # Different case and spacing, same normalized query: no second model call,
    # so the one-per-hour limit is not hit.
    second = client.get("/api/search/summary", params={"q": "  influenza "})
    third = client.get("/api/search/summary", params={"q": "rsv"})

    assert first.status_code == 200
    assert second.json() == first.json()
    assert third.status_code == 429


def test_over_the_daily_token_allowance_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "free_daily_tokens", 0)

    response = client.get("/api/search/summary", params={"q": "influenza"})

    assert response.status_code == 429


def test_no_model_configured_is_a_503(client: TestClient) -> None:
    app.state.search_summary = None

    response = client.get("/api/search/summary", params={"q": "influenza"})

    assert response.status_code == 503
