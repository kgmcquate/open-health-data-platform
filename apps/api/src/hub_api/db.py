"""Chat turn log, the thread list, and the daily quota counters
(docs/chatbot.md §6, §7).

Every chat turn is written to `chat_turns` — question, persona, plan, queries,
rows returned, citations, tokens — because that log *is* the eval set (§7), and
an eval set assembled later from prose alone cannot grade metric selection.
`threads` groups turns into the conversations the chat page's sidebar lists;
a turn's own row is still the unit of history (one question, one answer), not
a separate per-message table — reconstructing a thread's message list is
"every turn in order, user text then assistant text."

It is also the quota counter: "questions/tokens/dashboard actions since
midnight" is read straight off this table (`questions_today`, `tokens_today`,
`tool_calls_today`), so there is no second source of truth to drift. That
costs an index scan per question, which is nothing next to a model call.

Schema is created on startup with `create_all` rather than a migration tool —
honest for an append-only log, except `create_all` only creates missing
tables and never alters an existing one. `thread_id`/`feedback` postdate
`chat_turns` in any already-deployed database, so `_add_missing_columns`
below adds them by hand, idempotently, rather than pretending a migration
tool is here when it is not (see the ADD COLUMN calls for exactly where this
stops being honest and needs Alembic instead).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine

from ohdp_shared import get_logger

log = get_logger(__name__)

metadata = MetaData()

ThreadStatus = Literal["regular", "archived"]
Feedback = Literal["positive", "negative"]

threads = Table(
    "threads",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_email", String(320), nullable=False, index=True),
    Column("title", String(200), nullable=False, default=""),
    Column("status", String(16), nullable=False, default="regular"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, index=True),
    # SQLite only (Postgres identity columns never reuse a value): without
    # this, SQLite reissues a deleted row's rowid to the very next insert —
    # confirmed by hand, delete turn 9 then record a new one and it is also
    # turn 9. `chat_turns.id` feeds straight into the browser's message ids
    # (`u{id}`/`a{id}`), so a delete-then-resubmit (edit, regenerate) handed
    # the replacement turn the *same* id the discarded one had, and
    # assistant-ui — keying its reconciliation on that id — read "same id" as
    # "same message" and kept showing the stale bubble.
    sqlite_autoincrement=True,
)

chat_turns = Table(
    "chat_turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    # The verified identity from the auth wall, not anything the client sent.
    Column("user_email", String(320), nullable=False, index=True),
    Column("tier", String(16), nullable=False),
    Column("persona", String(128), nullable=False, default=""),
    # Nullable: turns logged before threads existed have none, and stay out
    # of every thread's history rather than being backfilled into one.
    Column("thread_id", ForeignKey("threads.id"), nullable=True, index=True),
    Column("question", String, nullable=False),
    Column("plan", String, nullable=False, default=""),
    Column("answer", String, nullable=False, default=""),
    # Structured columns, not a blob: §7 grades metric selection and refusal,
    # which means querying what the agent actually ran.
    Column("tool_calls", JSON, nullable=False),
    # The order text and tool calls actually happened in (`ohdp_agent.loop.Turn
    # .timeline`) — nullable because it postdates `chat_turns`, same as
    # `thread_id`/`feedback` below; a turn logged before this column existed
    # just falls back to rendering tool calls before the answer
    # (`apps/web/src/chat/runtime.ts`'s `turnToMessages`).
    Column("timeline", JSON, nullable=True),
    Column("queries", JSON, nullable=False),
    Column("citations", JSON, nullable=False),
    Column("stripped_citations", JSON, nullable=False),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("error", String, nullable=True),
    # "positive" | "negative" | NULL (no feedback given). One rating per turn.
    Column("feedback", String(16), nullable=True),
    # See threads' own sqlite_autoincrement comment — id reuse here is the
    # one that actually bit us, since this id is the one in message ids.
    sqlite_autoincrement=True,
)

# One row per *new* GitHub issue a signed-in reporter has had filed on their
# behalf (`hub_api.issues.report_issue`) — a duplicate report doesn't add a
# row, since nothing new was created. This is its own table rather than a
# `chat_turns` tool call because a report can come from the browser Support
# page directly, with no chat turn to attach it to.
filed_issues = Table(
    "filed_issues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("user_email", String(320), nullable=False, index=True),
    Column("github_number", Integer, nullable=False),
    sqlite_autoincrement=True,
)


def make_engine(url: str) -> Engine:
    """A pooled engine with pre-ping — the Postgres pod restarts on upgrades.

    SQLite (local dev only — see .env.example) gets neither: it's a file, not a
    server that restarts under you, and `pool_size`/`max_overflow` are QueuePool
    options that SQLAlchemy rejects on the SingletonThreadPool it picks for a
    file-based sqlite URL. It also needs its parent directory to exist, which
    a Postgres URL has no equivalent of.
    """
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, pool_pre_ping=True)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2)


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("chat_turns")}
    with engine.begin() as connection:
        if "thread_id" not in existing:
            connection.execute(text("ALTER TABLE chat_turns ADD COLUMN thread_id INTEGER"))
        if "feedback" not in existing:
            connection.execute(text("ALTER TABLE chat_turns ADD COLUMN feedback VARCHAR(16)"))
        if "timeline" not in existing:
            connection.execute(text("ALTER TABLE chat_turns ADD COLUMN timeline JSON"))

    # `subscriptions` postdates this function (hub_api.billing, M4); guarded on
    # `has_table` because db.py doesn't import billing.py and so can't assume
    # that table is registered on `metadata` the way chat_turns always is.
    if inspector.has_table("subscriptions"):
        sub_existing = {col["name"] for col in inspector.get_columns("subscriptions")}
        if "cancel_at" not in sub_existing:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE subscriptions ADD COLUMN cancel_at TIMESTAMP WITH TIME ZONE")
                    if engine.dialect.name == "postgresql"
                    else text("ALTER TABLE subscriptions ADD COLUMN cancel_at TIMESTAMP")
                )


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    _add_missing_columns(engine)
    log.info("chat_schema_ready")


def _start_of_today_la() -> datetime:
    """Midnight America/Los_Angeles, as of right now — converted to UTC.

    Pacific rather than UTC because that is where most of today's users are —
    "resets at midnight" should match the midnight they experience. Converted
    back to UTC before it is used in a query, deliberately: SQLite has no real
    timezone-aware column type, so an aware datetime is bound as a wall-clock
    string with the offset dropped. `chat_turns.created_at` is always written
    as `datetime.now(UTC)`; binding an LA wall clock against it directly
    compares two different wall clocks as if they were the same one and cuts
    the boundary 7-8 hours off. Converting to UTC first makes both sides the
    same wall clock again (see `test_tokens_today_cuts_at_los_angeles_midnight_
    not_utc_midnight` for the case that catches a regression here).
    """
    return (
        datetime.now(ZoneInfo("America/Los_Angeles"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
    )


def questions_today(engine: Engine, user_email: str) -> int:
    """Questions `user_email` has asked since midnight America/Los_Angeles.

    A COUNT over the log rather than a second counter, for the same reason
    `tokens_today` below is a SUM over it: there is no source of truth to drift
    from if there is only ever the one.
    """
    statement = (
        select(func.count())
        .select_from(chat_turns)
        .where(chat_turns.c.user_email == user_email)
        .where(chat_turns.c.created_at >= _start_of_today_la())
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def tokens_today(engine: Engine, user_email: str) -> int:
    """Input + output tokens `user_email` has spent since midnight America/Los_Angeles.

    Same shape as `questions_today` and for the same reason: the log is
    the only source of truth, so this is a SUM over it rather than a second
    counter that could drift from it.
    """
    statement = (
        select(func.coalesce(func.sum(chat_turns.c.input_tokens + chat_turns.c.output_tokens), 0))
        .where(chat_turns.c.user_email == user_email)
        .where(chat_turns.c.created_at >= _start_of_today_la())
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def tool_calls_today(engine: Engine, user_email: str, tool_name: str) -> int:
    """How many of `user_email`'s tool calls today were `tool_name`
    (e.g. `"render_dashboard"`, `"save_dashboard"`) — for capping a specific
    action separately from the overall token budget (`ohdp_agent.loop`'s
    `Turn.tool_calls`, persisted verbatim into `chat_turns.tool_calls`).

    Counted in Python over today's turns rather than as a JSON-aggregate SQL
    query: `tool_calls` is a JSON blob, and the two engines this runs
    against — SQLite locally, Postgres in prod — have no JSON-array syntax in
    common to write that count once. A user's turns for one day is a short
    list, so this costs a slightly wider read, not a slower one.
    """
    statement = (
        select(chat_turns.c.tool_calls)
        .where(chat_turns.c.user_email == user_email)
        .where(chat_turns.c.created_at >= _start_of_today_la())
    )
    with engine.connect() as connection:
        turns_tool_calls = connection.execute(statement).scalars().all()
    return sum(
        1
        for calls in turns_tool_calls
        for call in calls or []
        if call.get("name") == tool_name
    )


def issues_today(engine: Engine, user_email: str) -> int:
    """New GitHub issues filed on `user_email`'s behalf since midnight
    America/Los_Angeles — `hub_api.issues.report_issue`'s daily cap. A COUNT
    over `filed_issues`, same shape as `questions_today`, for the same reason:
    the log is the only source of truth."""
    statement = (
        select(func.count())
        .select_from(filed_issues)
        .where(filed_issues.c.user_email == user_email)
        .where(filed_issues.c.created_at >= _start_of_today_la())
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def record_filed_issue(engine: Engine, *, user_email: str, github_number: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            filed_issues.insert().values(
                created_at=datetime.now(UTC), user_email=user_email, github_number=github_number
            )
        )


def record_turn(
    engine: Engine,
    *,
    user_email: str,
    tier: str,
    persona: str,
    question: str,
    plan: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    citations: list[str],
    stripped_citations: list[str],
    input_tokens: int,
    output_tokens: int,
    error: str | None = None,
    thread_id: int | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> int | None:
    """Write one turn and return its id — `None` on failure, never a raise:
    losing the eval record must not lose the answer. The id is what the
    picker's thumbs-up/down (`set_feedback`) and edit/regenerate
    (`delete_turns_from`) address a specific turn by.
    """
    try:
        with engine.begin() as connection:
            now = datetime.now(UTC)
            result = connection.execute(
                chat_turns.insert().values(
                    created_at=now,
                    user_email=user_email,
                    tier=tier,
                    persona=persona,
                    thread_id=thread_id,
                    question=question,
                    plan=plan,
                    answer=answer,
                    tool_calls=tool_calls,
                    timeline=timeline or [],
                    queries=queries,
                    citations=citations,
                    stripped_citations=stripped_citations,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    error=error,
                )
            )
            if thread_id is not None:
                connection.execute(
                    update(threads).where(threads.c.id == thread_id).values(updated_at=now)
                )
            primary_key = result.inserted_primary_key
            return int(primary_key[0]) if primary_key is not None else None
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.error("chat_turn_not_logged", error=str(exc))
        return None


def create_thread(engine: Engine, *, user_email: str, title: str = "") -> int:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        result = connection.execute(
            threads.insert().values(
                user_email=user_email, title=title, status="regular", created_at=now, updated_at=now
            )
        )
        primary_key = result.inserted_primary_key
        assert primary_key is not None
        return int(primary_key[0])


def list_threads(engine: Engine, *, user_email: str) -> list[dict[str, Any]]:
    statement = (
        select(threads)
        .where(threads.c.user_email == user_email)
        .order_by(threads.c.updated_at.desc())
    )
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(statement)]


def get_thread(engine: Engine, *, thread_id: int, user_email: str) -> dict[str, Any] | None:
    statement = select(threads).where(threads.c.id == thread_id, threads.c.user_email == user_email)
    with engine.connect() as connection:
        row = connection.execute(statement).first()
        return dict(row._mapping) if row is not None else None


def rename_thread(engine: Engine, *, thread_id: int, user_email: str, title: str) -> None:
    statement = (
        update(threads)
        .where(threads.c.id == thread_id, threads.c.user_email == user_email)
        .values(title=title)
    )
    with engine.begin() as connection:
        connection.execute(statement)


def set_thread_status(
    engine: Engine, *, thread_id: int, user_email: str, status: ThreadStatus
) -> None:
    statement = (
        update(threads)
        .where(threads.c.id == thread_id, threads.c.user_email == user_email)
        .values(status=status)
    )
    with engine.begin() as connection:
        connection.execute(statement)


def delete_thread(engine: Engine, *, thread_id: int, user_email: str) -> None:
    """Deletes the thread and every turn logged under it — §7's eval set loses
    those rows too, the same trade every "delete my conversation" makes."""
    with engine.begin() as connection:
        connection.execute(
            delete(chat_turns).where(
                chat_turns.c.thread_id == thread_id, chat_turns.c.user_email == user_email
            )
        )
        connection.execute(
            delete(threads).where(threads.c.id == thread_id, threads.c.user_email == user_email)
        )


def thread_turns(engine: Engine, *, thread_id: int, user_email: str) -> list[dict[str, Any]]:
    """Every turn in the thread, oldest first — reconstructing it as messages
    (one user part, one assistant part, per turn) is the caller's job."""
    statement = (
        select(chat_turns)
        .where(chat_turns.c.thread_id == thread_id, chat_turns.c.user_email == user_email)
        .order_by(chat_turns.c.created_at.asc(), chat_turns.c.id.asc())
    )
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(statement)]


def delete_turns_from(engine: Engine, *, thread_id: int, user_email: str, turn_id: int) -> None:
    """Deletes `turn_id` and every turn after it in the thread — what an edited
    message or a regenerate needs before the edited/repeated question is
    resubmitted as a fresh turn. There is one branch per thread, not several:
    editing does not keep the discarded turns around to switch back to.
    """
    with engine.begin() as connection:
        cutoff = connection.execute(
            select(chat_turns.c.created_at, chat_turns.c.id).where(
                chat_turns.c.id == turn_id, chat_turns.c.user_email == user_email
            )
        ).first()
        if cutoff is None:
            return
        connection.execute(
            delete(chat_turns).where(
                chat_turns.c.thread_id == thread_id,
                chat_turns.c.user_email == user_email,
                chat_turns.c.created_at >= cutoff.created_at,
                chat_turns.c.id >= cutoff.id,
            )
        )


def set_feedback(engine: Engine, *, turn_id: int, user_email: str, feedback: Feedback) -> None:
    statement = (
        update(chat_turns)
        .where(chat_turns.c.id == turn_id, chat_turns.c.user_email == user_email)
        .values(feedback=feedback)
    )
    with engine.begin() as connection:
        connection.execute(statement)
