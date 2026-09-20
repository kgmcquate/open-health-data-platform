"""hub_api.db — threads, turn logging, edit/regenerate truncation, and the
"chat_turns predates thread_id/feedback" migration path — against a real
SQLite file (matching local dev; see db.make_engine's own docstring on why
`:memory:` isn't used here — it takes the Postgres-pool branch, not sqlite's).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Table, create_engine
from sqlalchemy.engine import Engine

from hub_api import db

USER = "researcher@example.org"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    e: Engine = db.make_engine(f"sqlite:///{tmp_path}/test.db")
    db.ensure_schema(e)
    return e


def _turn(
    engine: Engine, *, thread_id: int | None, question: str, answer: str, user_email: str = USER
) -> int:
    turn_id: int | None = db.record_turn(
        engine,
        user_email=user_email,
        tier="free",
        persona="",
        question=question,
        plan="",
        answer=answer,
        tool_calls=[],
        queries=[],
        citations=[],
        stripped_citations=[],
        input_tokens=1,
        output_tokens=1,
        thread_id=thread_id,
    )
    assert turn_id is not None
    return turn_id


def test_record_turn_returns_its_id_and_bumps_thread_updated_at(engine: Engine) -> None:
    thread_id = db.create_thread(engine, user_email=USER, title="first")
    before = db.get_thread(engine, thread_id=thread_id, user_email=USER)
    assert before is not None

    turn_id = _turn(engine, thread_id=thread_id, question="q1", answer="a1")

    after = db.get_thread(engine, thread_id=thread_id, user_email=USER)
    assert after is not None
    assert after["updated_at"] >= before["updated_at"]
    assert isinstance(turn_id, int)


def test_thread_turns_are_scoped_to_owner_and_ordered(engine: Engine) -> None:
    mine = db.create_thread(engine, user_email=USER, title="mine")
    theirs = db.create_thread(engine, user_email="other@example.org", title="theirs")
    _turn(engine, thread_id=mine, question="q1", answer="a1")
    _turn(engine, thread_id=mine, question="q2", answer="a2")
    _turn(
        engine,
        thread_id=theirs,
        question="not mine",
        answer="nope",
        user_email="other@example.org",
    )

    turns = db.thread_turns(engine, thread_id=mine, user_email=USER)

    assert [t["question"] for t in turns] == ["q1", "q2"]
    # A thread that is not this user's is simply invisible, not an error.
    assert db.thread_turns(engine, thread_id=theirs, user_email=USER) == []


def test_list_threads_orders_most_recently_active_first(engine: Engine) -> None:
    """Ordering follows `updated_at` — last *message* activity, bumped only by
    `record_turn` — not thread creation order or metadata edits like rename.
    """
    older = db.create_thread(engine, user_email=USER, title="older")
    newer = db.create_thread(engine, user_email=USER, title="newer")
    # Both threads' `updated_at` start equal to their own `created_at`; a turn
    # on `older` after `newer` already exists is what should move it to the
    # front, ahead of `newer`'s own (earlier) creation timestamp.
    _turn(engine, thread_id=older, question="q", answer="a")

    ids = [t["id"] for t in db.list_threads(engine, user_email=USER)]

    assert ids[0] == older
    assert newer in ids


def test_rename_thread_does_not_reorder_the_list(engine: Engine) -> None:
    older = db.create_thread(engine, user_email=USER, title="older")
    db.create_thread(engine, user_email=USER, title="newer")

    db.rename_thread(engine, thread_id=older, user_email=USER, title="renamed")

    # A rename is a metadata edit, not activity — it must not read as a newer
    # thread than one nobody has touched since it was created.
    ids = [t["id"] for t in db.list_threads(engine, user_email=USER)]
    assert ids[-1] == older


def test_a_new_turn_never_reuses_a_deleted_turns_id(engine: Engine) -> None:
    """SQLite's default rowid allocation reissues a deleted row's id to the
    very next insert; the frontend keys its message reconciliation off this
    id (`u{id}`/`a{id}`), so a resubmit-after-delete (edit, regenerate)
    handing the replacement the same id the discarded turn had read to the
    UI as "no change" and left the stale bubble on screen. sqlite_autoincrement
    on both tables (db.py) is what this asserts against regressing.
    """
    thread_id = db.create_thread(engine, user_email=USER)
    first = _turn(engine, thread_id=thread_id, question="q1", answer="a1")

    db.delete_turns_from(engine, thread_id=thread_id, user_email=USER, turn_id=first)
    second = _turn(engine, thread_id=thread_id, question="q1 edited", answer="a2")

    assert second != first
    assert second > first


def test_delete_turns_from_truncates_the_thread_at_that_point(engine: Engine) -> None:
    thread_id = db.create_thread(engine, user_email=USER)
    _turn(engine, thread_id=thread_id, question="q1", answer="a1")
    t2 = _turn(engine, thread_id=thread_id, question="q2", answer="a2")
    _turn(engine, thread_id=thread_id, question="q3", answer="a3")

    db.delete_turns_from(engine, thread_id=thread_id, user_email=USER, turn_id=t2)

    remaining = db.thread_turns(engine, thread_id=thread_id, user_email=USER)
    assert [t["question"] for t in remaining] == ["q1"]


def test_delete_turns_from_ignores_a_turn_id_that_is_not_the_users(engine: Engine) -> None:
    mine = db.create_thread(engine, user_email=USER)
    _turn(engine, thread_id=mine, question="q1", answer="a1")
    theirs_thread = db.create_thread(engine, user_email="other@example.org")
    theirs_turn = _turn(
        engine,
        thread_id=theirs_thread,
        question="not mine",
        answer="nope",
        user_email="other@example.org",
    )

    db.delete_turns_from(engine, thread_id=mine, user_email=USER, turn_id=theirs_turn)

    assert len(db.thread_turns(engine, thread_id=mine, user_email=USER)) == 1


def test_set_feedback_is_scoped_to_owner(engine: Engine) -> None:
    thread_id = db.create_thread(engine, user_email=USER)
    turn_id = _turn(engine, thread_id=thread_id, question="q", answer="a")

    db.set_feedback(engine, turn_id=turn_id, user_email=USER, feedback="positive")
    db.set_feedback(engine, turn_id=turn_id, user_email="other@example.org", feedback="negative")

    turns = db.thread_turns(engine, thread_id=thread_id, user_email=USER)
    assert turns[0]["feedback"] == "positive"


def test_archive_unarchive_and_delete_thread(engine: Engine) -> None:
    thread_id = db.create_thread(engine, user_email=USER)
    _turn(engine, thread_id=thread_id, question="q", answer="a")

    db.set_thread_status(engine, thread_id=thread_id, user_email=USER, status="archived")
    archived = db.get_thread(engine, thread_id=thread_id, user_email=USER)
    assert archived is not None
    assert archived["status"] == "archived"

    db.set_thread_status(engine, thread_id=thread_id, user_email=USER, status="regular")
    regular = db.get_thread(engine, thread_id=thread_id, user_email=USER)
    assert regular is not None
    assert regular["status"] == "regular"

    db.delete_thread(engine, thread_id=thread_id, user_email=USER)
    assert db.get_thread(engine, thread_id=thread_id, user_email=USER) is None
    assert db.thread_turns(engine, thread_id=thread_id, user_email=USER) == []


def test_ensure_schema_adds_missing_columns_to_a_pre_existing_table(tmp_path: Path) -> None:
    """`create_all` never alters a table that already exists — the real
    scenario `_add_missing_columns` exists for: a `chat_turns` created before
    `thread_id`/`feedback` were added to the Python schema.
    """
    url = f"sqlite:///{tmp_path}/legacy.db"
    engine = create_engine(url)
    legacy_metadata = db.metadata.__class__()
    legacy_columns = [
        c._copy() for c in db.chat_turns.columns if c.name not in ("thread_id", "feedback")
    ]
    Table("chat_turns", legacy_metadata, *legacy_columns)
    legacy_metadata.create_all(engine)

    fresh_engine = db.make_engine(url)
    db.ensure_schema(fresh_engine)

    thread_id = db.create_thread(fresh_engine, user_email=USER)
    turn_id = _turn(fresh_engine, thread_id=thread_id, question="q", answer="a")
    db.set_feedback(fresh_engine, turn_id=turn_id, user_email=USER, feedback="positive")
    assert db.thread_turns(fresh_engine, thread_id=thread_id, user_email=USER)[0]["feedback"] == (
        "positive"
    )
