"""The thread-list and feedback endpoints (hub_api.chat) — ownership scoping,
not the agent itself (that's ohdp_agent's own test_loop_agent.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.engine import Engine

from hub_api import chat, db
from hub_api.main import app
from hub_api.models import ModelConfig
from ohdp_agent.loop import Deps

USER = "researcher@example.org"
OTHER = "other@example.org"


def _minimal_agent() -> Agent[Deps, str]:
    """A no-op agent for tests that only need the registry metadata."""

    async def _noop(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, Any]]:
        if False:
            yield ""

    return Agent(FunctionModel(stream_function=_noop), deps_type=Deps, toolsets=[])


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine: Engine = db.make_engine(f"sqlite:///{tmp_path}/test.db")
    db.ensure_schema(engine)
    app.dependency_overrides[chat.get_engine] = lambda: engine
    app.dependency_overrides[chat.get_user_email] = lambda: USER
    client = TestClient(app)
    # Override the agent registry after lifespan has run so the /models endpoint
    # sees predictable entries without needing real API keys.
    app.state.agents = {
        "openrouter/llama-3": ModelConfig(
            agent=_minimal_agent(), label="Llama 3", configured=False
        ),
        "openai/gpt-4o-mini": ModelConfig(agent=_minimal_agent(), label="GPT 4", configured=True),
        "openai/gpt-4o": ModelConfig(agent=_minimal_agent(), label="GPT-4o", configured=False),
    }
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_create_list_and_get_thread(client: TestClient) -> None:
    created = client.post("/api/threads", json={"title": "My conversation"})
    assert created.status_code == 200
    thread_id = created.json()["id"]

    listed = client.get("/api/threads").json()
    assert [t["id"] for t in listed] == [thread_id]

    fetched = client.get(f"/api/threads/{thread_id}").json()
    assert fetched["title"] == "My conversation"
    assert fetched["turns"] == []


def test_thread_belonging_to_another_user_404s(client: TestClient, tmp_path: Path) -> None:
    engine: Engine = app.dependency_overrides[chat.get_engine]()
    their_thread = db.create_thread(engine, user_email=OTHER, title="not yours")

    response = client.get(f"/api/threads/{their_thread}")

    assert response.status_code == 404


def test_rename_archive_unarchive_delete(client: TestClient) -> None:
    thread_id = client.post("/api/threads", json={}).json()["id"]

    renamed = client.patch(f"/api/threads/{thread_id}", json={"title": "New name"})
    assert renamed.json()["title"] == "New name"

    archived = client.post(f"/api/threads/{thread_id}/archive")
    assert archived.json()["status"] == "archived"

    unarchived = client.post(f"/api/threads/{thread_id}/unarchive")
    assert unarchived.json()["status"] == "regular"

    deleted = client.delete(f"/api/threads/{thread_id}")
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/threads/{thread_id}").status_code == 404


def test_feedback_is_scoped_to_the_submitting_user(client: TestClient) -> None:
    engine: Engine = app.dependency_overrides[chat.get_engine]()
    thread_id = db.create_thread(engine, user_email=USER)
    turn_id = db.record_turn(
        engine,
        user_email=USER,
        tier="free",
        persona="",
        thread_id=thread_id,
        question="q",
        plan="",
        answer="a",
        tool_calls=[],
        queries=[],
        citations=[],
        stripped_citations=[],
        input_tokens=1,
        output_tokens=1,
    )
    assert turn_id is not None

    response = client.post("/api/feedback", json={"turn_id": turn_id, "rating": "positive"})

    assert response.status_code == 200
    turns = db.thread_turns(engine, thread_id=thread_id, user_email=USER)
    assert turns[0]["feedback"] == "positive"


def test_feedback_rejects_an_unknown_rating(client: TestClient) -> None:
    response = client.post("/api/feedback", json={"turn_id": 1, "rating": "meh"})

    assert response.status_code == 422


def test_models_lists_only_configured_models_from_models_yaml(client: TestClient) -> None:
    response = client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert [m["id"] for m in body] == ["openai/gpt-4o-mini"]
    assert body[0] == {"id": "openai/gpt-4o-mini", "label": "GPT 4", "default": False}


def test_models_returns_empty_when_no_models_are_configured(client: TestClient) -> None:
    app.state.agents = {
        "openrouter/llama-3": ModelConfig(
            agent=_minimal_agent(), label="Llama 3", configured=False
        ),
        "openai/gpt-4o": ModelConfig(agent=_minimal_agent(), label="GPT-4o", configured=False),
    }
    response = client.get("/api/models")
    assert response.status_code == 200
    assert response.json() == []


def test_answering_a_question_nobody_is_waiting_on_is_a_404(client: TestClient) -> None:
    """`/api/chat/answer` resolves an in-flight `ask_user` (ohdp_agent.loop).
    An unknown, expired or someone-else's ask_id is the same 404 — the browser
    is never told which, for the same reason a thread it does not own 404s."""
    response = client.post("/api/chat/answer", json={"ask_id": "deadbeef", "answer": "Ages 35+"})
    assert response.status_code == 404


def test_answering_reaches_the_run_waiting_on_that_question(client: TestClient) -> None:
    import asyncio

    from ohdp_agent.loop import AskRegistry, PendingAsk

    loop = asyncio.new_event_loop()
    try:
        future: asyncio.Future[str] = loop.create_future()
        pending: AskRegistry = {"abc123": PendingAsk(owner=USER, future=future)}
        client.app.state.pending_asks = pending

        # Another signed-in user cannot answer this user's question, even
        # holding its id.
        client.app.dependency_overrides[chat.get_user_email] = lambda: OTHER
        assert (
            client.post("/api/chat/answer", json={"ask_id": "abc123", "answer": "no"}).status_code
            == 404
        )
        assert not future.done()

        client.app.dependency_overrides[chat.get_user_email] = lambda: USER
        ok = client.post("/api/chat/answer", json={"ask_id": "abc123", "answer": "Ages 35+"})
        assert ok.status_code == 200
        assert future.result() == "Ages 35+"

        # The future is resolved, so a second click gets the same 404 as a
        # question that never existed.
        assert (
            client.post(
                "/api/chat/answer", json={"ask_id": "abc123", "answer": "again"}
            ).status_code
            == 404
        )
    finally:
        client.app.state.pending_asks = {}
        loop.close()
