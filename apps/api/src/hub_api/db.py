"""Chat turn log and the monthly quota counter (docs/chatbot.md §6, §7).

One table. Every chat turn is written to it — question, persona, plan, queries,
rows returned, citations, tokens — because that log *is* the eval set (§7), and
an eval set assembled later from prose alone cannot grade metric selection.

It is also the quota counter: "questions asked this calendar month" is a count
over this table, so there is no second source of truth to drift. That costs an
index scan per question, which is nothing next to a model call.

Schema is created on startup with `create_all` rather than a migration tool.
That is honest for one append-only table; the moment a column needs to change
shape under live data, this needs Alembic instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine

from ohdp_shared import get_logger

log = get_logger(__name__)

metadata = MetaData()

chat_turns = Table(
    "chat_turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    # The verified identity from the auth wall, not anything the client sent.
    Column("user_email", String(320), nullable=False, index=True),
    Column("tier", String(16), nullable=False),
    Column("persona", String(128), nullable=False, default=""),
    Column("question", String, nullable=False),
    Column("plan", String, nullable=False, default=""),
    Column("answer", String, nullable=False, default=""),
    # Structured columns, not a blob: §7 grades metric selection and refusal,
    # which means querying what the agent actually ran.
    Column("tool_calls", JSON, nullable=False),
    Column("queries", JSON, nullable=False),
    Column("citations", JSON, nullable=False),
    Column("stripped_citations", JSON, nullable=False),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("error", String, nullable=True),
)


def make_engine(url: str) -> Engine:
    """A pooled engine with pre-ping — the Postgres pod restarts on upgrades."""
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2)


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    log.info("chat_schema_ready")


def questions_this_month(engine: Engine, user_email: str) -> int:
    """Questions `user_email` has asked since the start of the current UTC month.

    Calendar-month, not rolling-30-day: it is what a user can predict, and it is
    what a monthly subscription implies.
    """
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    statement = (
        select(func.count())
        .select_from(chat_turns)
        .where(chat_turns.c.user_email == user_email)
        .where(chat_turns.c.created_at >= start)
    )
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


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
) -> None:
    """Write one turn. Never raises — losing the eval record must not lose the answer."""
    try:
        with engine.begin() as connection:
            connection.execute(
                chat_turns.insert().values(
                    created_at=datetime.now(UTC),
                    user_email=user_email,
                    tier=tier,
                    persona=persona,
                    question=question,
                    plan=plan,
                    answer=answer,
                    tool_calls=tool_calls,
                    queries=queries,
                    citations=citations,
                    stripped_citations=stripped_citations,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    error=error,
                )
            )
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.error("chat_turn_not_logged", error=str(exc))
