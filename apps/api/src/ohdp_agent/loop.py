"""The chat agent's tool-use loop (docs/chatbot.md §4), built on pydantic-ai.

An OpenAI-spec chat-completions backend (OpenRouter, self-hosted vLLM/Ollama,
Azure OpenAI, etc.) configured via `OHDP_OPENAI_API_BASE_URLS`/`OHDP_OPENAI_API_KEYS`,
streamed over up to three tool groups: catalog context (OpenMetadata), execution
(Cube), and literature (Europe PMC) — each either this module's in-process
implementation (`CUBE_TOOLSET`, `LITERATURE_TOOLSET`, `_catalog_toolset`) or an
MCP connection from `apps/api/config/tools.yaml`, per model, entirely per
`apps/api/config/models.yaml`'s `tools:` list (`hub_api.models.build_agents`)
— nothing here is attached by default. No raw SQL tool exists anywhere in the
surface — §2.2, ADR-0003.

**Why pydantic-ai instead of the raw OpenAI SDK.** hub-api used to drive the
chat-completions API by hand (reassembling streamed tool-call argument fragments
itself). pydantic-ai's `Agent` runs the turn loop and the tool-call round trip
for us, so `Deps`/`_run_tool` below is the *only* thing this module owns —
everything else it used to take to keep model APIs in sync is gone. What it does
not give us for free is this app's specific shape: the SSE `Event` stream
`apps/web/src/chat/runtime.ts` already renders, the "plan is the first thing the
model writes" rule (§4, rule 2), and the citation check — those still live here.

Everything a tool returns — catalog descriptions, glossary terms, article
abstracts — is untrusted input that lands in the prompt (§6). It is passed to the
model as tool results, never merged into the system prompt, and the model is told
as much below. The one exception is the persona preamble (§3.1), which is
curated-by-a-human catalog content and is deliberately trusted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.usage import UsageLimits

from ohdp_agent.catalog import CatalogClient, CatalogError, ToolSpec
from ohdp_agent.cube import CubeClient, CubeError
from ohdp_agent.literature import LiteratureClient, LiteratureError, unverified_citations
from ohdp_agent.models import CubeQuery
from ohdp_agent.render import catalog_json, cube_json
from ohdp_shared import get_logger

log = get_logger(__name__)

MAX_TOKENS = 16_000

# A turn is one model request. Twenty gives the plan-then-execute shape room
# for a multi-cube question (list_metrics, several describe_metric calls, a
# rejected query retried, run_metric_query, explain_query, a literature
# search) while still bounding the cost of a run that decides to keep
# exploring the catalog. Also used as the per-tool retry budget
# (`Agent(retries=...)`) so a fixable mistake — a bad member name, a
# rejected query — is never the bottleneck; the overall step count still is.
MAX_TURNS = 50

# Population-level, not clinical decision support (ARCHITECTURE.md §10.2). This
# is attached to the answer by the caller, not requested from the model — a
# disclaimer the model can forget to write is not a disclaimer.
DISCLAIMER = (
    "This answers from population-level public health data. "
    "It is not clinical decision support and not medical advice."
)

# `ask_user` — the model asking the reader a question mid-answer, rendered as
# buttons in the chat (docs/chatbot.md §4b). The tool blocks the run until the
# browser POSTs back the chosen option, so the model keeps its whole context
# across the question instead of having to end the turn and re-derive it from
# a fresh one — this app deliberately passes no `message_history` to
# `agent.run`, so a question asked by ending a turn would be forgotten by the
# turn that answered it.
#
# Five minutes is the bound on that block. The SSE stream stays alive on
# keepalives (`hub_api.chat.KEEPALIVE`) for as long as it, but the model run,
# its context and a queue slot are all held open the whole time, so this is a
# real resource bound — long enough to read three options and click one, short
# enough that a tab left open over lunch does not pin a run indefinitely.
ASK_USER_TIMEOUT_SECONDS = 300.0

# Two to six. One "option" is not a choice, and a list longer than this is a
# menu the reader has to study rather than a question they can answer at a
# glance — at which point the model should narrow it down itself, which is
# what it has the catalog tools for.
MIN_ASK_OPTIONS = 2
MAX_ASK_OPTIONS = 6


@dataclass
class PendingAsk:
    """One `ask_user` call waiting on the browser.

    `owner` is the signed-in email of the run that asked, checked before any
    answer is accepted: `ask_id` is a uuid4 and unguessable, but "unguessable"
    is not an authorisation model, and this registry is shared by every
    in-flight run in the process.
    """

    owner: str
    future: asyncio.Future[str]


# ask_id -> the run waiting on it. Owned by hub-api (one dict on `app.state`,
# `hub_api.chat`) rather than by this module, because the thing that resolves
# an entry is a *different HTTP request* from the one that created it — the
# browser POSTing the chosen option while the asking request is still holding
# its SSE stream open. Process-local, which is what the single-replica
# `Recreate` deployment makes safe; a second replica would need the answer
# routed to the pod holding the stream, not just to any pod.
AskRegistry = dict[str, PendingAsk]


@dataclass
class AskUserChannel:
    """The asking half of `AskRegistry`, scoped to one run and one user."""

    owner: str
    pending: AskRegistry
    timeout: float = ASK_USER_TIMEOUT_SECONDS

    async def ask(self, ask_id: str) -> str | None:
        """Block until someone resolves `ask_id`, or `timeout` elapses (None)."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending[ask_id] = PendingAsk(owner=self.owner, future=future)
        try:
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError:
            return None
        finally:
            # Also covers the cancellation path — the reader navigating away
            # cancels the whole run, and this entry must not outlive it.
            self.pending.pop(ask_id, None)


def resolve_ask(pending: AskRegistry, *, ask_id: str, owner: str, answer: str) -> bool:
    """Hand `answer` to the run waiting on `ask_id`. False if nothing is.

    False covers every "that question is over" case the browser can hit —
    unknown id, a question that already timed out, a run that was cancelled,
    and another user's question — deliberately without distinguishing them,
    for the same reason `hub_api.chat._get_owned_thread` 404s rather than 403s.
    """
    entry = pending.get(ask_id)
    if entry is None or entry.owner != owner or entry.future.done():
        return False
    entry.future.set_result(answer)
    return True


# Follow-up suggestions — the "what next?" chips under the latest answer.
# The model is told to end its final reply with a sentinel-delimited JSON array
# of three follow-up questions, and `_extract_followups` splits that block back
# off before the answer is shown, citation-checked, or logged. This costs zero
# extra model calls (the run that answers also proposes what to ask next, with
# the full question/tool context already in hand — a separate suggestion call
# would double the per-turn cost on a quota-metered surface), and a model that
# ignores the instruction simply yields no suggestions rather than a broken
# answer. The split happens on the sentinel whether or not what follows parses:
# a malformed block must never leak into the answer the reader sees.
FOLLOW_UPS_SENTINEL = "<<<FOLLOW-UPS>>>"

FOLLOW_UPS_INSTRUCTIONS = f"""

After the answer — this is separate from the plan, and nothing may follow it — \
propose exactly three follow-up questions that a reader of your answer would \
plausibly ask next. Each one must be answerable from this platform's semantic \
layer or literature tools, and each must be under 120 characters. Each must \
also stand on its own as a new question: you will not see this conversation \
again, so a reply like "yes, do that" or "publish it" reaches you with nothing \
attached and cannot be acted on. End your \
reply with this exact block, and nothing after it:

{FOLLOW_UPS_SENTINEL}
["first follow-up question", "second follow-up question", "third follow-up question"]\
"""

SYSTEM_PROMPT = """\
This prompt is the system message for the Open Health Data Platform's chat agent.
"""


@dataclass
class Event:
    """One thing worth telling the UI about. Serialised as an SSE `data:` line."""

    type: str
    data: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"type": self.type, **self.data}, separators=(",", ":"))


@dataclass
class Turn:
    """What one question cost and what it touched — the eval record (§7).

    hub-api writes this to Postgres. It is the eval set, so it holds the plan and
    the queries, not just the prose.
    """

    question: str
    persona: str
    plan: str = ""
    answer: str = ""
    error: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # The chronological order text and tool calls actually happened in — a
    # `{"type": "text", "text": ...}` or `{"type": "tool_call", "tool_call_id":
    # ...}` per entry, the latter pointing back into `tool_calls` above rather
    # than duplicating it. `answer`/`tool_calls` stay the flat shape §7's eval
    # set has always used; this exists only so a reloaded thread can rebuild
    # the tool-call chips in the position they actually streamed in, instead
    # of grouping every call before the answer text
    # (`apps/web/src/chat/runtime.ts`'s `turnToMessages`).
    timeline: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    stripped_citations: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Deps:
    """Per-request state every tool function reads from `ctx.deps`.

    Nothing here is closed over by a tool function — every function takes
    `ctx: RunContext[Deps]` and reads through it — which is what lets
    `CUBE_TOOLSET`/`LITERATURE_TOOLSET` be module-level objects built once and
    reused by every model and every request that is given one, rather than
    rebuilt per question the way the pre-pydantic-ai loop had to.
    """

    cube: CubeClient
    literature: LiteratureClient
    catalog: CatalogClient | None
    turn: Turn
    queue: asyncio.Queue[Event]
    # The `ask_user` tool's way back to the browser, or None when this run has
    # no reader waiting on it (an eval harness, a test). The tool tells the
    # model to answer without asking in that case rather than blocking on a
    # channel nobody is listening to.
    ask: AskUserChannel | None = None
    # Text from every model request this run makes, in order — not just the
    # last one. pydantic-ai's own `result.output` is only the final request's
    # text; the answer this app shows is the plan *and* the result narrative
    # together (rule 2 and rule 4 of the system prompt above are both "the
    # answer"), so this is accumulated by `_handle_stream` across the whole run.
    answer_parts: list[str] = field(default_factory=list)
    # Deltas for the text part currently being streamed. If the run is cancelled
    # before the corresponding PartEndEvent, this buffer is flushed to the turn
    # so the UI does not lose the latest streamed state.
    current_text: str = ""


def _query_schema() -> dict[str, Any]:
    """`CubeQuery`'s JSON schema, inlined and closed to extra properties."""
    schema = CubeQuery.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def cube_tool_specs() -> list[dict[str, Any]]:
    """The four execution tools (§2.2), described for the model.

    `run_metric_query`'s schema is generated from `CubeQuery` rather than written
    out here, so the thing the model is told it may send and the thing we accept
    cannot drift apart. The validation still happens on our side — the schema is
    guidance, `CubeQuery(**args)` is the control.
    """
    return [
        {
            "name": "list_metrics",
            "description": (
                "Every cube, measure and dimension in the semantic layer. This is the "
                "complete set of things that can be queried. Call this before planning "
                "any query. If something is not here, the platform does not have it."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "describe_metric",
            "description": (
                "One cube in detail: its measures, dimensions, types and descriptions. "
                "Call after list_metrics narrows the field."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cube_name": {
                        "type": "string",
                        "description": "The cube's name, exactly as list_metrics returned it.",
                    }
                },
                "required": ["cube_name"],
            },
        },
        {
            "name": "run_metric_query",
            "description": (
                "Run a query against the semantic layer. Members are "
                "'cube_name.field_name' exactly as list_metrics returned them. There is "
                "no SQL here and no way to express one: anything outside this schema is "
                "rejected before the query is sent. Ask for as few rows as the question "
                "needs — the default `limit` (100) is enough for almost everything; "
                "raise it only when the answer genuinely requires seeing more rows than "
                "that, not as a hedge."
            ),
            "input_schema": _query_schema(),
        },
        {
            "name": "explain_query",
            "description": (
                "The compiled SQL for a query, so the reader can see where a number came "
                "from. Call this on the same query you just ran."
            ),
            "input_schema": _query_schema(),
        },
    ]


def literature_tool_specs() -> list[dict[str, Any]]:
    """Europe PMC search (§2.3)."""
    return [
        {
            "name": "search_literature",
            "description": (
                "Search Europe PMC (PubMed plus preprints) by title and abstract. "
                "Returns structured records with PMID/PMCID/DOI. Only identifiers "
                "returned here may be cited."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms."},
                    "limit": {
                        "type": "integer",
                        "description": "How many records to return. Default 10, max 25.",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_article",
            "description": "One Europe PMC record by PMID, PMCID or DOI.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "A PMID, PMCID or DOI."}
                },
                "required": ["identifier"],
            },
        },
    ]


async def _run_tool(ctx: RunContext[Deps], name: str, arguments: dict[str, Any]) -> str:
    """Shared dispatch body for every built-in and catalog tool.

    Raising `ModelRetry` is pydantic-ai's "the model can fix this itself":
    the message reaches the model as a tool result it can act on, same as a
    rejected query always could. `list_metrics`/`run_metric_query`/etc. also
    queue the specific event (`rows`, `sql`, `articles`) a human watching the
    chat page reads live, which pydantic-ai has no equivalent of. The
    `tool_call` event itself and `turn.tool_calls` are *not* set here, even
    though this is the one place that knows `name` and `arguments` without
    re-deriving them — `_handle_stream` is what has to see every tool call to
    log one made through an operator-configured connection (tools.yaml) that
    never runs through this function at all, so it is the only place that can
    be the single source for either without one of the two tool surfaces
    silently going unlogged.
    """
    deps = ctx.deps
    turn = deps.turn
    try:
        if name == "list_metrics":
            cubes = await deps.cube.list_metrics()
            return json.dumps(catalog_json(cubes), separators=(",", ":"))
        if name == "describe_metric":
            cube = await deps.cube.describe_metric(str(arguments.get("cube_name", "")))
            return json.dumps(cube_json(cube, with_agg=True), separators=(",", ":"))
        if name == "run_metric_query":
            query = CubeQuery(**arguments)
            result = await deps.cube.run_metric_query(query)
            turn.queries.append({"query": query.to_cube_json(), "rows": result["row_count"]})
            await deps.queue.put(
                Event(
                    "rows",
                    {
                        "query": query.to_cube_json(),
                        "rows": result["rows"],
                        "row_count": result["row_count"],
                        "truncated": result["truncated"],
                    },
                )
            )
            return json.dumps(result, separators=(",", ":"), default=str)
        if name == "explain_query":
            sql = await deps.cube.explain_query(CubeQuery(**arguments))
            await deps.queue.put(Event("sql", {"sql": sql}))
            return sql
        if name == "search_literature":
            articles = await deps.literature.search_literature(
                str(arguments.get("query", "")),
                limit=int(arguments.get("limit", 10)),
            )
            await deps.queue.put(
                Event("articles", {"articles": [_article_json(a) for a in articles]})
            )
            return json.dumps([_article_json(a) for a in articles], separators=(",", ":"))
        if name == "get_article":
            article = await deps.literature.get_article(str(arguments.get("identifier", "")))
            return json.dumps(_article_json(article), separators=(",", ":"))
        if deps.catalog is not None:
            return await deps.catalog.call_tool(name, arguments)
        raise CatalogError(f"unknown tool {name!r}")
    except ValidationError as exc:
        # The model built a query outside the allowed shape. This is the
        # safety control doing its job, and the model can usually fix it.
        log.info("query_rejected", tool=name, errors=exc.error_count())
        message = "Query rejected by validation."
        await deps.queue.put(Event("tool_error", {"name": name, "message": message}))
        raise ModelRetry(f"Rejected: {exc}"[:2000]) from exc
    except (CubeError, LiteratureError, CatalogError) as exc:
        log.info("tool_failed", tool=name, error=str(exc))
        await deps.queue.put(Event("tool_error", {"name": name, "message": str(exc)}))
        raise ModelRetry(str(exc)[:2000]) from exc


def _tool_from_spec(spec: dict[str, Any]) -> Tool[Deps]:
    name = spec["name"]

    async def call(ctx: RunContext[Deps], **kwargs: Any) -> str:
        return await _run_tool(ctx, name, kwargs)

    return Tool.from_schema(
        function=call,
        name=name,
        description=spec["description"],
        json_schema=spec["input_schema"],
        takes_ctx=True,
    )


def _catalog_tool(spec: ToolSpec) -> Tool[Deps]:
    async def call(ctx: RunContext[Deps], **kwargs: Any) -> str:
        return await _run_tool(ctx, spec.name, kwargs)

    return Tool.from_schema(
        function=call,
        name=spec.name,
        description=spec.description,
        json_schema=spec.input_schema,
        takes_ctx=True,
    )


# Cube and literature tools, described once and shared by every model and
# every request that is actually given them — the functions above all read
# `ctx.deps` at call time rather than closing over any particular request's
# clients, which is what lets one module-level object be reused this way.
#
# This module has no opinion on which model gets which — that resolution is
# entirely `hub_api.models.build_agents`'s, driven by a model's `tools:` list
# in `apps/api/config/models.yaml` (its own comment is the operator side of
# this). Nothing here is attached to a model by default. A model that lists
# both "cube" and `mcp-cube` gets the same four tools registered twice under
# the same names, which pydantic-ai refuses to build an agent with; that is an
# operator error to avoid, not something resolved automatically, since only
# the operator can say which implementation a given model should actually call.
CUBE_TOOLSET: FunctionToolset[Deps] = FunctionToolset(
    [_tool_from_spec(spec) for spec in cube_tool_specs()]
)
LITERATURE_TOOLSET: FunctionToolset[Deps] = FunctionToolset(
    [_tool_from_spec(spec) for spec in literature_tool_specs()]
)


def ask_user_tool_spec() -> dict[str, Any]:
    """The one tool that asks rather than answers (§4b)."""
    return {
        "name": "ask_user",
        "description": (
            "Ask the person you are answering to choose between a small number of "
            "concrete options, and wait for their reply. The options are rendered as "
            "buttons under the chat box, so this is how to resolve an ambiguity the "
            "catalog cannot: which of several similar metrics they meant, which "
            "geography or year range, which of two readings of the question. Prefer it "
            "over writing a question in prose, which only ends the turn without "
            "getting an answer. Do not use it to confirm a choice you can make "
            "yourself, or to ask the same thing twice. Permission is the one "
            "exception: a question whose answer you have to act on yourself — "
            "publishing something outside this conversation is the case that "
            "matters — has to be asked here, because a question written in prose "
            "ends the turn, and the reply comes back to a run that remembers "
            "none of what it was about. The call "
            "blocks until they answer, so ask one question at a time, and include "
            "everything they need to choose in the question itself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "One short question, in plain language.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"Between {MIN_ASK_OPTIONS} and {MAX_ASK_OPTIONS} answers to "
                        "choose between. Each is a button label, so keep each one "
                        "under 80 characters and make it stand on its own."
                    ),
                },
                "allow_other": {
                    "type": "boolean",
                    "description": (
                        "Also offer a free-text box, for when the options may not "
                        "cover what they meant. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["question", "options"],
        },
    }


async def _ask_user(ctx: RunContext[Deps], **kwargs: Any) -> str:
    """Put the question on the wire and block until the browser answers it.

    Not routed through `_run_tool` like the cube/literature/catalog tools:
    those all dispatch to a client on `ctx.deps` and share one failure
    translation, and this one has no client, no remote error to translate, and
    the opposite data flow — it is the only tool whose result comes from the
    reader rather than from the platform.

    Every `ModelRetry` here is a malformed call the model can fix on the spot
    (`Agent(retries=...)` covers it), and the timeout is deliberately *not* one:
    asking again is exactly the wrong response to nobody being there to answer.
    """
    deps = ctx.deps
    if deps.ask is None:
        raise ModelRetry(
            "There is no one to ask on this channel. Answer with what you have, "
            "stating which reading of the question you assumed."
        )

    question = str(kwargs.get("question", "")).strip()
    if not question:
        raise ModelRetry("ask_user needs a `question`.")
    raw_options = kwargs.get("options") or []
    if not isinstance(raw_options, list):
        raise ModelRetry("`options` must be a list of strings.")
    # De-duplicated because the answer comes back as the option's own text:
    # two identical buttons would be two ways to send the same reply, and a
    # reader cannot tell which one the model meant by either.
    options: list[str] = []
    for option in raw_options:
        text = str(option).strip()
        if text and text not in options:
            options.append(text)
    if len(options) < MIN_ASK_OPTIONS:
        raise ModelRetry(
            f"ask_user needs at least {MIN_ASK_OPTIONS} distinct options. "
            "If there is only one sensible answer, take it and say so instead."
        )
    options = options[:MAX_ASK_OPTIONS]

    ask_id = uuid4().hex
    await deps.queue.put(
        Event(
            "ask_user",
            {
                # The matching `tool_call` event (from `_handle_stream`) and
                # this one can reach the browser in either order — they are
                # produced by different coroutines onto the same queue — so
                # this carries the question and options itself rather than
                # having the UI read them off the tool call's arguments.
                "tool_call_id": ctx.tool_call_id or "",
                "ask_id": ask_id,
                "question": question,
                "options": options,
                "allow_other": bool(kwargs.get("allow_other", False)),
            },
        )
    )
    answer = await deps.ask.ask(ask_id)
    if answer is None:
        log.info("ask_user_timeout", ask_id=ask_id)
        await deps.queue.put(Event("ask_cancelled", {"ask_id": ask_id}))
        return (
            "No answer — the reader did not reply in time. Do not ask again: "
            "continue with the most reasonable option and say which one you assumed. "
            "If the question was permission to do something that reaches outside "
            "this conversation — publishing, saving, sending — silence is a no: "
            "do not do it, and say that you did not."
        )
    log.info("ask_user_answered", ask_id=ask_id)
    # Free text from the reader when `allow_other` was set, so it is untrusted
    # input like any other tool result (§6) — it reaches the model as a tool
    # result, never as an instruction, which is what the system prompt already
    # tells it about every tool's output.
    return answer


# Separate from CUBE/LITERATURE so an operator can give a model the ability to
# ask without giving it anything else, and — more to the point — so that a
# model on a surface with no reader attached simply is not given it.
ASK_USER_TOOLSET: FunctionToolset[Deps] = FunctionToolset(
    [
        Tool.from_schema(
            function=_ask_user,
            name="ask_user",
            description=ask_user_tool_spec()["description"],
            json_schema=ask_user_tool_spec()["input_schema"],
            takes_ctx=True,
        )
    ]
)


async def _catalog_toolset(catalog: CatalogClient | None) -> FunctionToolset[Deps] | None:
    """Catalog tools, discovered fresh per request (OM's advertised set can
    change between deploys) — this is the one part of the tool surface that
    cannot be built once at startup the way `CUBE_TOOLSET`/`LITERATURE_TOOLSET`
    above are.

    Only called when `run`'s `include_catalog_tools` is True — a model's
    `tools:` list has to name "catalog" to get this in-process path, the same
    as it has to name "cube"/"literature" to get those, or `mcp-openmetadata`
    to get OpenMetadata's tools over MCP instead. Nothing is attached by
    default (`apps/api/config/models.yaml`).
    """
    if catalog is None:
        return None
    try:
        specs = await catalog.list_tools()
    except CatalogError as exc:
        # The catalog being down should cost context, not the answer.
        log.warning("catalog_tools_unavailable", error=str(exc))
        return None
    return FunctionToolset([_catalog_tool(spec) for spec in specs]) if specs else None


def _looks_like_html_document(text: str) -> bool:
    """Whether a tool result is a full HTML document rather than data.

    Narrow on purpose: every other tool result in this loop is `json.dumps`
    output, SQL text, or plain prose, none of which starts this way. The one
    thing that does is `render_dashboard` (`hub_api.dashboards.render_html`),
    reached through the `ohdp-tools` toolset (`hub_api.main`'s `lifespan`,
    built by `hub_api.tool_connections.build_local_openapi_toolset`) — its
    HTTP `Content-Disposition: inline` embed header is stripped by the MCP
    wrapper (`hub_api.tool_connections`), so this is the only signal left on
    this side of that boundary that the result is a page to render, not text
    to print.
    """
    head = text.lstrip()[:100].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def _tool_result_payload(event: FunctionToolResultEvent) -> dict[str, Any]:
    """The `tool_result` event body for one completed tool call.

    `RetryPromptPart` (a rejected query, a validation error, a raised
    `ModelRetry`) has no `outcome` — reaching the model as a retry request is
    itself the failure signal — so it is always reported as an error here.
    """
    part = event.part
    if isinstance(part, RetryPromptPart):
        return {
            "tool_call_id": event.tool_call_id,
            "is_error": True,
            "result": part.model_response(),
        }

    text = part.model_response_str(wrap_if_error=False)
    payload: dict[str, Any] = {
        "tool_call_id": event.tool_call_id,
        "is_error": part.outcome != "success",
        "result": text,
    }
    if _looks_like_html_document(text):
        payload["format"] = "html"
    return payload


async def _handle_stream(ctx: RunContext[Deps], events: AsyncIterable[AgentStreamEvent]) -> None:
    """Forward text/thinking deltas live, log every tool call, and build this
    turn's text from `PartEndEvent` rather than the deltas themselves.

    The `tool_call`/`tool_result` events and `turn.tool_calls` are reported
    from `FunctionToolCallEvent`/`FunctionToolResultEvent` here, not
    self-reported by `_run_tool`, because this is the only place that sees a
    tool call regardless of which toolset it came from — `CUBE_TOOLSET`/`LITERATURE_TOOLSET`,
    the catalog's per-request tools, *and* an operator-configured MCP/OpenAPI
    connection from tools.yaml, which pydantic-ai's own `MCPToolset`
    dispatches without ever going through `_run_tool` at all. Self-reporting
    inside `_run_tool` would leave every tools.yaml connection invisible to
    both the UI and the eval log — this was caught by manually driving a real
    OpenAPI connection end to end, not by a unit test, since a mocked tool
    call has no independent event stream to disagree with the code under
    test. The two events share `tool_call_id`, which is what lets the
    frontend attach a result — and, for `render_dashboard`'s HTML, an
    embedded chart — to the call it belongs to (`apps/web/src/chat/runtime.ts`).

    A backend that hands back a whole text part in one piece — no incremental
    chunks — emits `PartStartEvent`/`PartEndEvent` with the full content and
    *no* `PartDeltaEvent` at all (confirmed against pydantic-ai's own
    `FunctionModel` test double, and not guaranteed absent from a real
    OpenAI-spec server either). Building the answer from deltas alone silently
    drops that text; `PartEndEvent` is always present and always carries the
    complete part, so it is the correctness source. Deltas are still forwarded
    live purely for the token-by-token UI — losing that for such a backend
    means the text only appears at the end, not that it goes missing.
    """
    text_parts: list[str] = []
    async for event in events:
        if isinstance(event, PartStartEvent):
            # A new part's first chunk of content rides on PartStartEvent, not
            # a PartDeltaEvent — pydantic-ai's own part manager folds it into
            # the part it constructs before any delta is emitted. Forwarding
            # only deltas here dropped that opening chunk from the live UI on
            # every part after the first (most visibly: the first word of the
            # text that follows a tool-call/thinking part).
            if isinstance(event.part, TextPart) and event.part.content:
                await ctx.deps.queue.put(Event("text", {"text": event.part.content}))
                ctx.deps.current_text += event.part.content
            elif isinstance(event.part, ThinkingPart) and event.part.content:
                await ctx.deps.queue.put(Event("thinking", {"text": event.part.content}))
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta):
                await ctx.deps.queue.put(Event("text", {"text": delta.content_delta}))
                ctx.deps.current_text += delta.content_delta
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await ctx.deps.queue.put(Event("thinking", {"text": delta.content_delta}))
        elif isinstance(event, PartEndEvent) and isinstance(event.part, TextPart):
            text_parts.append(event.part.content)
        elif isinstance(event, FunctionToolCallEvent):
            arguments = event.part.args_as_dict()
            ctx.deps.turn.tool_calls.append(
                {
                    "tool_call_id": event.tool_call_id,
                    "name": event.part.tool_name,
                    "input": arguments,
                }
            )
            ctx.deps.turn.timeline.append({"type": "tool_call", "tool_call_id": event.tool_call_id})
            await ctx.deps.queue.put(
                Event(
                    "tool_call",
                    {
                        "tool_call_id": event.tool_call_id,
                        "name": event.part.tool_name,
                        "input": arguments,
                    },
                )
            )
        elif isinstance(event, FunctionToolResultEvent):
            payload = _tool_result_payload(event)
            await ctx.deps.queue.put(Event("tool_result", payload))
            # Folded into the matching call's own dict (by tool_call_id) rather
            # than a separate `turn` list — `chat_turns.tool_calls` is what a
            # reloaded thread has to rebuild the tool-call/tool-result bubble
            # from (`apps/web/src/chat/runtime.ts`'s `turnToMessages`), so the
            # persisted shape needs to already be call+result paired, the same
            # way the live `tool_call`/`tool_result` events pair up by id.
            for call in ctx.deps.turn.tool_calls:
                if call.get("tool_call_id") == payload["tool_call_id"]:
                    call["result"] = payload["result"]
                    call["is_error"] = payload["is_error"]
                    if "format" in payload:
                        call["format"] = payload["format"]
                    break

    text = "".join(text_parts)
    # Some backends emit the full part in one shot (no deltas) while others
    # stream deltas. If the run is cancelled before PartEndEvent, fall back to
    # the buffered deltas so the persisted turn still reflects the latest UI.
    streamed = text or ctx.deps.current_text
    if not streamed:
        return
    ctx.deps.answer_parts.append(streamed)
    ctx.deps.current_text = ""
    ctx.deps.turn.timeline.append({"type": "text", "text": streamed})
    # The first prose the model writes is its plan (rule 2, §4).
    if not ctx.deps.turn.plan:
        ctx.deps.turn.plan = streamed
        await ctx.deps.queue.put(Event("plan", {"text": streamed}))


def build_agent(
    *,
    model_id: str,
    base_url: str,
    api_key: str = "",
    extra_toolsets: Sequence[AbstractToolset[Deps]] = (),
) -> Agent[Deps, str]:
    """One reusable `Agent` for `model_id` — built once per configured model
    (`hub_api.models`), not per request. Persona instructions and the
    catalog's per-request tools are supplied at run time instead (`run`,
    below), which is what lets the same `Agent` serve every user and persona.

    Only OpenAI-spec chat-completions backends are supported. `base_url` must
    point to one (OpenRouter, self-hosted vLLM/Ollama, Azure OpenAI, etc.).

    `extra_toolsets` is this model's entire configured tool surface — this
    module's own `CUBE_TOOLSET`/`LITERATURE_TOOLSET` and `apps/api/config/tools.yaml`
    connections alike, resolved from that model's `tools:` list in models.yaml
    by `hub_api.models.build_agents` (docs/chatbot.md §4a). Nothing is added on
    top by default: an id not listed there is a tool this model does not get.
    """
    # The openai SDK refuses to construct a client with an empty api_key at all
    # (`OpenAIError: Missing credentials`) — even for a local server that ignores
    # auth entirely, so a keyless backend (settings.py explicitly allows one)
    # needs *something* non-empty here.
    provider = OpenAIProvider(base_url=base_url, api_key=api_key or "not-required")
    model = OpenAIChatModel(model_id, provider=provider)
    model_settings = ModelSettings(max_tokens=MAX_TOKENS)
    return Agent(
        model,
        deps_type=Deps,
        toolsets=list(extra_toolsets),
        model_settings=model_settings,
        retries=MAX_TURNS,
    )


async def run(
    agent: Agent[Deps, str],
    *,
    question: str,
    persona: str,
    turn: Turn,
    queue: asyncio.Queue[Event],
    cube: CubeClient,
    literature: LiteratureClient,
    catalog: CatalogClient | None,
    system_prompt: str = SYSTEM_PROMPT,
    include_catalog_tools: bool = False,
    ask: AskUserChannel | None = None,
) -> None:
    """Answer `question`, pushing events onto `queue` as they happen and
    filling `turn`. Always ends by pushing a `done` or `error` event — the
    caller does not need its own except-clause for the ordinary failure modes.

    The caller has already checked entitlement and quota — by the time this
    runs, the question is paid for (§6).

    `system_prompt` defaults to the built-in prompt (its hard rules included)
    but a model configured in `models.yaml` can replace it outright — see that
    file's own comment for why that is an explicit, informed operator choice
    and not merged with the default.

    `include_catalog_tools` is True only for a model whose `tools:` list names
    "catalog" (`hub_api.models.build_agents`) — the in-process path, discovered
    fresh per request below since OM's advertised tools can change between
    deploys. A model that instead names `mcp-openmetadata` gets OpenMetadata's
    tools already baked into `agent`'s own toolsets at build time, and passes
    False here — building `_catalog_toolset` too would register the same tool
    names twice. A model that names neither gets no catalog tools at all.
    `catalog` is still used for the persona preamble regardless (below) — that
    is context assembly, not a tool, and never collides.

    `ask` is the live reader behind this run, for the `ask_user` tool. `None`
    — the default — means nobody is watching, and `ask_user` (if this model
    even has it) says so to the model rather than blocking. Giving a model the
    tool and giving a run somewhere to ask are two separate decisions on
    purpose: the toolset comes from models.yaml, this comes from the surface
    the run is happening on.
    """
    deps = Deps(cube=cube, literature=literature, catalog=catalog, turn=turn, queue=queue, ask=ask)
    await queue.put(Event("status", {"message": "Reading the catalog"}))

    instructions = system_prompt
    if catalog is not None and persona:
        preamble = await catalog.persona_preamble(persona)
        if preamble:
            instructions += (
                "\n\nContext for the person you are answering, curated in the "
                f"data catalog:\n\n{preamble}"
            )
    # Appended here rather than baked into SYSTEM_PROMPT so a models.yaml
    # override that replaces the prompt outright still gets follow-ups — and
    # still gets its block split back off — the same as every other model.
    instructions += FOLLOW_UPS_INSTRUCTIONS

    extra_toolsets: list[AbstractToolset[Deps]] = []
    if include_catalog_tools:
        catalog_toolset = await _catalog_toolset(catalog)
        if catalog_toolset is not None:
            extra_toolsets.append(catalog_toolset)

    try:
        result = await agent.run(
            question,
            deps=deps,
            instructions=instructions,
            toolsets=extra_toolsets,
            event_stream_handler=_handle_stream,
            usage_limits=UsageLimits(request_limit=MAX_TURNS),
        )
    except UsageLimitExceeded:
        log.warning("agent_turn_limit", limit=MAX_TURNS)
        turn.error = "Gave up after too many steps without an answer."
        await queue.put(Event("error", {"message": turn.error}))
        return
    except AgentRunError as exc:
        log.error("model_error", error=str(exc))
        turn.error = "Something went wrong answering that."
        await queue.put(Event("error", {"message": turn.error}))
        return
    except asyncio.CancelledError:
        turn.error = "Generation was cancelled."
        raise
    finally:
        # A failed tool call, a model error, or the user pressing stop can all
        # leave the run without a final answer. Persist whatever text we did
        # manage to accumulate — including deltas for a part that never got its
        # PartEndEvent — so the UI can show the latest state instead of having
        # the message disappear on reload.
        if deps.current_text:
            turn.timeline.append({"type": "text", "text": deps.current_text})
        turn.answer = "\n\n".join([*deps.answer_parts, deps.current_text]).strip()

    usage = result.usage
    turn.input_tokens += usage.input_tokens
    turn.output_tokens += usage.output_tokens

    answer = "\n\n".join(deps.answer_parts).strip()
    raw_len = len(answer)
    # Split the follow-ups block off before the citation check runs: the
    # questions are prompts to send next, not facts asserted in this answer,
    # and the reader never sees the block either way.
    answer, suggestions = _extract_followups(answer)
    _trim_timeline_tail(turn.timeline, raw_len - len(answer))
    answer, stripped = _check_citations(answer, literature)
    _strip_timeline_citations(turn.timeline, stripped)
    turn.answer = answer
    turn.stripped_citations = stripped
    turn.citations = sorted(literature.registry.seen)

    if stripped:
        await queue.put(
            Event(
                "warning",
                {
                    "message": (
                        "Some references in this answer did not come from a literature "
                        "search and were removed."
                    ),
                    "stripped": stripped,
                },
            )
        )
    # Follow-ups are ephemeral — carried on the `done` event for the live UI,
    # not persisted with the turn — so a reloaded thread shows its answers but
    # no stale chips for whatever happened to be the last live question.
    await queue.put(
        Event(
            "done",
            {"answer": answer, "disclaimer": DISCLAIMER, "suggestions": suggestions},
        )
    )


def _article_json(article: Any) -> dict[str, str]:
    return {
        "pmid": article.pmid,
        "pmcid": article.pmcid,
        "doi": article.doi,
        "title": article.title,
        "authors": article.authors,
        "journal": article.journal,
        "year": article.year,
        "abstract": article.abstract[:2000],
    }


def _extract_followups(text: str) -> tuple[str, list[str]]:
    """Split the `<<<FOLLOW-UPS>>>` block off the end of a model reply.

    Returns `(answer, suggestions)` — up to three questions, empty when the
    model did not comply or the block did not parse. Tolerates a fenced
    ```json block around the array, since models like to add one.
    """
    index = text.rfind(FOLLOW_UPS_SENTINEL)
    if index == -1:
        return text, []
    answer = text[:index].rstrip()
    remainder = text[index + len(FOLLOW_UPS_SENTINEL) :].strip()
    if remainder.startswith("```"):
        # Drop the opening fence line (``` or ```json) and any closing fence.
        remainder = remainder.split("\n", 1)[-1] if "\n" in remainder else remainder[3:]
        remainder = remainder.removesuffix("```").strip()
    try:
        parsed = json.loads(remainder)
    except ValueError:
        return answer, []
    if not isinstance(parsed, list):
        return answer, []
    questions = [q.strip() for q in parsed if isinstance(q, str) and 4 <= len(q.strip()) <= 200]
    return answer, questions[:3]


def _trim_timeline_tail(timeline: list[dict[str, Any]], trim: int) -> None:
    """Cut `trim` characters off the end of `timeline`'s text entries — the
    same number `_extract_followups` just cut off the tail of
    `"\\n\\n".join(answer_parts)` — so `turn.timeline` stays in lockstep with
    `turn.answer` (both trace back to the same raw segments, joined the same
    way). Walks entries back to front, crossing the same `"\\n\\n"` join
    separators the original string had between them, and drops any text entry
    the trim empties out entirely (the whole follow-ups block was its own
    trailing segment).
    """
    if trim <= 0:
        return
    text_indices = [i for i, e in enumerate(timeline) if e["type"] == "text"]
    for rank, i in enumerate(reversed(text_indices)):
        if trim <= 0:
            break
        if rank > 0:
            trim -= min(trim, 2)  # the "\n\n" separator before this entry
            if trim <= 0:
                break
        text = timeline[i]["text"]
        take = min(trim, len(text))
        timeline[i]["text"] = text[: len(text) - take]
        trim -= take
    timeline[:] = [e for e in timeline if e["type"] != "text" or e["text"]]


def _strip_timeline_citations(timeline: list[dict[str, Any]], stripped: list[str]) -> None:
    """Mirror `_check_citations`'s redaction onto each timeline text entry —
    a plain substring replace, safe to apply per-entry since an identifier
    never spans two of them."""
    for identifier in stripped:
        for entry in timeline:
            if entry["type"] == "text":
                entry["text"] = entry["text"].replace(identifier, "[unverified citation removed]")


def _check_citations(answer: str, literature: LiteratureClient) -> tuple[str, list[str]]:
    """Strip citations no tool returned (§2.3).

    Redacting rather than deleting is deliberate: a reader who sees
    `[unverified citation removed]` knows something was taken out, where a
    silently-deleted DOI just reads as a sentence with a missing reference.
    """
    unverified = unverified_citations(answer, literature.registry)
    for identifier in unverified:
        answer = answer.replace(identifier, "[unverified citation removed]")
    return answer, unverified
