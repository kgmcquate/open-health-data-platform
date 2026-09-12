"""`/api/chat` — the quota gate, the agent, and the SSE stream (docs/chatbot.md §4).

Order of operations matters and is the whole point of this module:

    identity  ->  tier  ->  quota  ->  model  ->  stream  ->  log

The quota check happens *before* the agent is constructed, so a user over their
limit costs us an index scan rather than an Opus call (§6, ARCHITECTURE.md §10.3).

**Identity comes from the auth wall, never from the request body.** As deployed
today, `app.open-health-data-platform.org` sits behind an oauth2-proxy Google
wall (the same pattern as Dagster and Streamlit), which sets `X-Forwarded-Email`
on every request it passes. hub-api's own Ingress is disabled, so the proxy is
the only route in from outside the cluster.

That makes the header trustworthy from the internet and *not* trustworthy from
inside the cluster — any pod could call the Service directly and set it itself.
That is the same posture Dagster and dagster-monitoring already run under, and
it is acceptable while every pod in the cluster is ours. It stops being
acceptable the moment ARCHITECTURE.md §5's real OIDC provider arrives and `tier`
starts driving billing: at that point the session must be verified here, and
`tier` must come from a signed claim rather than from `TIER`, below.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import anthropic
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from hub_api import db
from ohdp_agent.catalog import CatalogClient
from ohdp_agent.cube import CubeClient
from ohdp_agent.literature import LiteratureClient
from ohdp_agent.loop import ChatAgent, Event, Turn
from ohdp_shared import get_logger, settings

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# Every user is `free` until the OIDC provider of ARCHITECTURE.md §5 exists and
# can put a real `tier` claim in a session. Named rather than inlined so the
# place that has to change is obvious.
TIER = "free"

# Sent every 15s so the proxy and any intermediary see traffic on a long plan
# phase. An SSE comment line, ignored by EventSource.
KEEPALIVE = ": keepalive\n\n"
KEEPALIVE_SECONDS = 15.0


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Explicit, not inferred — "explicit is honest and testable" (§10.4).
    persona: str = Field(default="", max_length=128)


def get_engine(request: Request) -> Engine:
    """The chat-log pool. Declared *after* the identity dependency on every route
    so an unauthenticated caller never learns whether our database is up."""
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The chat log database is not available.")
    return engine


def get_user_email(
    x_forwarded_email: Annotated[str | None, Header()] = None,
) -> str:
    """The email the auth wall verified. No header, no chat."""
    if not x_forwarded_email:
        raise HTTPException(401, "Not signed in.")
    return x_forwarded_email


@router.get("/me")
def me(
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    """Who the wall says you are and what you have left this month."""
    used = db.questions_this_month(engine, user_email)
    return {
        "email": user_email,
        "tier": TIER,
        "questions_used": used,
        "questions_allowed": _allowance(),
    }


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> StreamingResponse:
    """Answer one question, streamed as Server-Sent Events.

    The quota is checked here, before anything expensive is built. It is counted
    from the turn log, which is written when the turn finishes — so N genuinely
    simultaneous requests from one user can each see the same pre-request count
    and all pass. With one replica and a single-operator auth wall that race is
    theoretical; it becomes real when §5's tiers do, and the fix then is a
    reservation row rather than a count.
    """
    allowance = _allowance()
    used = db.questions_this_month(engine, user_email)
    if used >= allowance:
        log.info("quota_exceeded", user=user_email, used=used, allowance=allowance)
        raise HTTPException(
            429,
            f"You have used all {allowance} questions included this month.",
        )

    if not settings.anthropic_api_key:
        raise HTTPException(503, "The chat agent is not configured (no model API key).")

    return StreamingResponse(
        _stream(engine, body, user_email),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Traefik does not buffer, but say so anyway — this response is
            # useless if anything between here and the browser holds it.
            "X-Accel-Buffering": "no",
        },
    )


async def _stream(engine: Engine, body: ChatRequest, user_email: str) -> AsyncIterator[str]:
    """Drive the agent, forward its events, and log the turn when it ends."""
    turn = Turn(question=body.question, persona=body.persona)
    agent = ChatAgent(
        client=anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
        cube=CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=TIER),
        catalog=(
            CatalogClient(settings.openmetadata_url, settings.openmetadata_jwt)
            if settings.openmetadata_jwt
            else None
        ),
        literature=LiteratureClient(),
        persona=body.persona,
    )

    failure: str | None = None
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def produce() -> None:
        nonlocal failure
        try:
            async for event in agent.run(body.question, turn):
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 — the stream must always close cleanly
            log.exception("chat_failed", user=user_email)
            failure = str(exc)
            await queue.put(Event("error", {"message": "Something went wrong answering that."}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield KEEPALIVE
                continue
            if event is None:
                break
            yield f"data: {event.to_json()}\n\n"
    finally:
        # A browser that navigates away cancels this generator mid-flight; the
        # turn still gets logged, because a question that cost tokens counts
        # against quota whether or not anyone read the answer.
        task.cancel()
        db.record_turn(
            engine,
            user_email=user_email,
            tier=TIER,
            persona=turn.persona,
            question=turn.question,
            plan=turn.plan,
            answer=turn.answer,
            tool_calls=turn.tool_calls,
            queries=turn.queries,
            citations=turn.citations,
            stripped_citations=turn.stripped_citations,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            error=failure,
        )
        log.info(
            "chat_turn",
            user=user_email,
            tools=len(turn.tool_calls),
            queries=len(turn.queries),
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )


def _allowance() -> int:
    return settings.paid_monthly_questions if TIER == "paid" else settings.free_monthly_questions
