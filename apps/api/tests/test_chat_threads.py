"""The thread-list and feedback endpoints (hub_api.chat) — ownership scoping,
not the agent itself (that's ohdp_agent's own test_loop_agent.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from hub_api import chat, db
from hub_api.main import app

USER = "researcher@example.org"
OTHER = "other@example.org"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine: Engine = db.make_engine(f"sqlite:///{tmp_path}/test.db")
    db.ensure_schema(engine)
    app.dependency_overrides[chat.get_engine] = lambda: engine
    app.dependency_overrides[chat.get_user_email] = lambda: USER
    try:
        yield TestClient(app)
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
