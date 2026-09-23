"""`/api/chat` — the quota gate, the agent, and the SSE stream (docs/chatbot.md §4).

Order of operations matters and is the whole point of this module:

    identity  ->  tier  ->  quota  ->  model  ->  stream  ->  log

The quota check happens *before* the agent is constructed, so a user over their
limit costs us an index scan rather than an Opus call (§6, ARCHITECTURE.md §10.3).

**Identity comes from the hub's own signed session cookie, never from the
request body.** `app.open-health-data-platform.org` reaches hub-api's Ingress
directly — there is no oauth2-proxy wall in front of it any more (hub_api.auth
does its own OIDC, ARCHITECTURE.md §5, gated by `settings.allowed_emails_list`
until billing (M4) can meter strangers). `tier` still comes from `TIER`, below,
rather than a signed claim, because the IdP does not issue one yet.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from hub_api import db
from hub_api.models import AgentRegistry, ModelConfig
from ohdp_agent.catalog import CatalogClient
from ohdp_agent.cube import CubeClient
from ohdp_agent.literature import LiteratureClient
from ohdp_agent.loop import AskRegistry, AskUserChannel, Event, Turn
from ohdp_agent.loop import resolve_ask as resolve_agent_ask
from ohdp_agent.loop import run as run_agent
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


MAX_IMAGES_PER_QUESTION = 4
TITLE_MAX_LENGTH = 60


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Explicit, not inferred — "explicit is honest and testable" (§10.4).
    persona: str = Field(default="", max_length=128)
    # Must be one of the ids returned by GET /api/models. The browser never
    # sends a base_url or key — that would let the client choose what hub-api
    # talks to.
    model: str = Field(default="", max_length=256)
    # Every question belongs to a thread — the chat page creates one
    # (POST /api/threads) before the first message, never implicitly here.
    thread_id: int
    # Set for an edited message or a regenerate: the turn to discard, and
    # every turn after it, before this question is asked. Both are "resubmit
    # from here" to a flat turn log — an edit changes what `question` is
    # first; the response's own thumbs-up/down and edit history do not
    # survive past that point, the same way editing a message in most chat
    # UIs abandons the branch it replaces.
    truncate_from_turn_id: int | None = None
    # Data URLs from the composer's image attachments (`data:image/...;base64,...`)
    # — never a file path or a URL hub-api would have to fetch itself.
    images: list[str] = Field(default_factory=list, max_length=MAX_IMAGES_PER_QUESTION)


class ThreadCreate(BaseModel):
    title: str = Field(default="", max_length=TITLE_MAX_LENGTH)


class ThreadRename(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)


class FeedbackRequest(BaseModel):
    turn_id: int
    rating: Literal["positive", "negative"]


class AskAnswer(BaseModel):
    """The reader's reply to an `ask_user` question still waiting on them."""

    ask_id: str = Field(min_length=1, max_length=64)
    # Normally one of the options the model offered, but not checked against
    # them: `allow_other` questions accept free text, and the model is told
    # either way that a tool result is data and not an instruction (§6). The
    # length cap is the same one a typed question gets.
    answer: str = Field(min_length=1, max_length=2000)


def get_engine(request: Request) -> Engine:
    """The chat-log pool. Declared *after* the identity dependency on every route
    so an unauthenticated caller never learns whether our database is up."""
    engine: Engine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(503, "The chat log database is not available.")
    return engine


def get_agents(request: Request) -> AgentRegistry:
    """model id -> its reusable Agent, built once at startup (hub_api.models)."""
    return getattr(request.app.state, "agents", {})


def get_pending_asks(request: Request) -> AskRegistry:
    """Every `ask_user` question currently waiting on a browser (ohdp_agent.loop).

    Created in `hub_api.main`'s lifespan; defaulted here so a test that builds
    the app without it still works, the same way `get_agents` tolerates an
    empty registry. This is the one piece of chat state that has to be shared
    *between* requests — the POST that answers a question is not the request
    holding the stream that asked it — which is also why it is process-local
    state and not a database row: it is only meaningful to the event loop that
    is still awaiting it.
    """
    pending: AskRegistry | None = getattr(request.app.state, "pending_asks", None)
    if pending is None:
        pending = {}
        request.app.state.pending_asks = pending
    return pending


@router.get("/models")
def models(agents: Annotated[AgentRegistry, Depends(get_agents)]) -> list[dict[str, object]]:
    """The models explicitly listed in config/models.yaml, for the picker —
    not the full registry, which also holds every auto-discovered model an
    OpenAI-spec backend key can see. Those stay usable by id (an existing
    thread, or a direct API call) but are not listed in the picker.
    """
    return [
        {"id": model_id, "label": agents[model_id].label, "default": False}
        for model_id in sorted(agents)
        if agents[model_id].configured
    ]


def get_user_email(request: Request) -> str:
    """Identity from the hub's own OIDC session (hub_api.auth)."""
    user = request.session.get("user")
    if user and user.get("email"):
        return str(user["email"])
    raise HTTPException(401, "Not signed in.")


def get_optional_user_email(request: Request) -> str | None:
    """The same identity, but `None` instead of a 401 when signed out.

    For a public route that shows a *little* more to someone who is signed in —
    `hub_api.content`'s dashboard listing, which fills in which way the viewer
    voted. A dependency rather than an inline `request.session` read so the
    identity rules stay in one module and a test can override it.
    """
    user = request.session.get("user")
    return str(user["email"]) if user and user.get("email") else None


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


def _get_owned_thread(engine: Engine, *, thread_id: int, user_email: str) -> dict[str, object]:
    """A thread that is not this user's 404s exactly like one that does not
    exist — the alternative (403) confirms the id is real, which is its own
    small leak of another user's data."""
    thread = db.get_thread(engine, thread_id=thread_id, user_email=user_email)
    if thread is None:
        raise HTTPException(404, "No such thread.")
    return thread


@router.get("/threads")
def list_threads(
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[dict[str, object]]:
    return db.list_threads(engine, user_email=user_email)


@router.post("/threads")
def create_thread(
    body: ThreadCreate,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    thread_id = db.create_thread(engine, user_email=user_email, title=body.title)
    return _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)


@router.get("/threads/{thread_id}")
def get_thread(
    thread_id: int,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    """Thread metadata plus every turn in it — the chat page's history load
    when switching to (or reopening) a conversation."""
    thread = _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)
    turns = db.thread_turns(engine, thread_id=thread_id, user_email=user_email)
    return {**thread, "turns": turns}


@router.patch("/threads/{thread_id}")
def rename_thread(
    thread_id: int,
    body: ThreadRename,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)
    db.rename_thread(engine, thread_id=thread_id, user_email=user_email, title=body.title)
    return _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)


@router.post("/threads/{thread_id}/archive")
def archive_thread(
    thread_id: int,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)
    db.set_thread_status(engine, thread_id=thread_id, user_email=user_email, status="archived")
    return _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)


@router.post("/threads/{thread_id}/unarchive")
def unarchive_thread(
    thread_id: int,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, object]:
    _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)
    db.set_thread_status(engine, thread_id=thread_id, user_email=user_email, status="regular")
    return _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)


@router.delete("/threads/{thread_id}")
def delete_thread(
    thread_id: int,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, bool]:
    _get_owned_thread(engine, thread_id=thread_id, user_email=user_email)
    db.delete_thread(engine, thread_id=thread_id, user_email=user_email)
    return {"ok": True}


@router.post("/feedback")
def submit_feedback(
    body: FeedbackRequest,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> dict[str, bool]:
    db.set_feedback(engine, turn_id=body.turn_id, user_email=user_email, feedback=body.rating)
    return {"ok": True}


@router.post("/chat/answer")
async def answer_ask(
    body: AskAnswer,
    user_email: Annotated[str, Depends(get_user_email)],
    pending: Annotated[AskRegistry, Depends(get_pending_asks)],
) -> dict[str, bool]:
    """Hand the reader's choice to the `/api/chat` stream that is blocked on it.

    A second request, deliberately: the asking request is busy holding its SSE
    response open, and SSE is one-directional. 404 covers every way a question
    can no longer be waiting — answered, timed out, cancelled, or another
    user's — without saying which (`resolve_ask`).

    **`async def`, not `def`, and that is load-bearing.** FastAPI runs a sync
    endpoint on a worker thread, and `asyncio.Future.set_result` — which is what
    `resolve_ask` does — is not thread-safe: off the event loop's own thread it
    can set the value without ever scheduling the callback that wakes the
    coroutine awaiting it, leaving the chat run blocked until its five-minute
    timeout despite the answer having arrived. There is nothing blocking in this
    handler to justify a worker thread anyway: it touches one dict.

    No quota check: the question that is waiting was already paid for when it
    was asked, and this route neither starts a run nor spends a token.
    """
    if not resolve_agent_ask(pending, ask_id=body.ask_id, owner=user_email, answer=body.answer):
        raise HTTPException(404, "That question is no longer waiting for an answer.")
    return {"ok": True}


def _decode_image(data_url: str) -> tuple[bytes, str]:
    """`data:<media_type>;base64,<payload>` -> the pair `loop.run`'s `images`
    wants. Raises `HTTPException` rather than a bare parse error — this comes
    straight from the client, `Depends`-free, so nothing upstream has vetted it.
    """
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise HTTPException(400, "Attachments must be base64 data URLs.")
    header, _, payload = data_url.partition(";base64,")
    media_type = header.removeprefix("data:") or "application/octet-stream"
    try:
        return base64.b64decode(payload, validate=True), media_type
    except binascii.Error as exc:
        raise HTTPException(400, "An attachment's image data is not valid base64.") from exc


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user_email: Annotated[str, Depends(get_user_email)],
    engine: Annotated[Engine, Depends(get_engine)],
    agents: Annotated[AgentRegistry, Depends(get_agents)],
    pending: Annotated[AskRegistry, Depends(get_pending_asks)],
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

    model = body.model
    if not model:
        if not agents:
            raise HTTPException(503, "The chat agent is not configured (no model API key).")
        raise HTTPException(400, "A model must be selected. See GET /api/models.")
    config = agents.get(model)
    if config is None:
        raise HTTPException(400, f"Unknown model {model!r}. See GET /api/models.")

    thread = _get_owned_thread(engine, thread_id=body.thread_id, user_email=user_email)
    if body.truncate_from_turn_id is not None:
        # An edited message or a regenerate: discard the old branch before
        # this question becomes the thread's new last turn.
        db.delete_turns_from(
            engine,
            thread_id=body.thread_id,
            user_email=user_email,
            turn_id=body.truncate_from_turn_id,
        )
    if not thread["title"]:
        title = (
            body.question
            if len(body.question) <= TITLE_MAX_LENGTH
            else (body.question[: TITLE_MAX_LENGTH - 1] + "…")
        )
        db.rename_thread(engine, thread_id=body.thread_id, user_email=user_email, title=title)

    images = [_decode_image(url) for url in body.images]

    # Scoped to this user so `/api/chat/answer` can check who is allowed to
    # answer a given question without the browser being trusted to say.
    ask = AskUserChannel(owner=user_email, pending=pending)

    return StreamingResponse(
        _stream(engine, body, user_email, config, images, ask),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Traefik does not buffer, but say so anyway — this response is
            # useless if anything between here and the browser holds it.
            "X-Accel-Buffering": "no",
        },
    )


async def _stream(
    engine: Engine,
    body: ChatRequest,
    user_email: str,
    config: ModelConfig,
    images: list[tuple[bytes, str]],
    ask: AskUserChannel,
) -> AsyncIterator[str]:
    """Drive the agent, forward its events, and log the turn when it ends."""
    turn = Turn(question=body.question, persona=body.persona)
    cube = CubeClient(settings.cube_api_url, settings.cube_api_secret, tier=TIER)
    catalog = (
        CatalogClient(settings.openmetadata_url, settings.openmetadata_jwt)
        if settings.openmetadata_jwt
        else None
    )
    literature = LiteratureClient()

    failure: str | None = None
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def produce() -> None:
        nonlocal failure
        try:
            await run_agent(
                config.agent,
                question=body.question,
                persona=body.persona,
                turn=turn,
                queue=queue,  # type: ignore[arg-type]
                cube=cube,
                literature=literature,
                catalog=catalog,
                system_prompt=config.system_prompt,
                images=images,
                include_catalog_tools=config.include_catalog_tools,
                ask=ask,
            )
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
        # against quota whether or not anyone read the answer. Wait for the
        # producer to finish so loop.run() has flushed any partial answer into
        # the turn before we record it.
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — log and continue to record_turn
            log.exception("chat_produce_failed", user=user_email)
            failure = failure or str(exc)

        # Ensure the persisted turn always carries either text the user saw or
        # an explanation of why there is none, so the message never disappears
        # on a later reload.
        failure = failure or turn.error or None
        if not turn.answer and not failure:
            failure = "Generation was cancelled or produced no answer."

        db.record_turn(
            engine,
            user_email=user_email,
            tier=TIER,
            persona=turn.persona,
            thread_id=body.thread_id,
            question=turn.question,
            plan=turn.plan,
            answer=turn.answer,
            tool_calls=turn.tool_calls,
            timeline=turn.timeline,
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
