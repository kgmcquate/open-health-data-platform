"""`ohdp_agent.loop.run`, end to end, against pydantic-ai's `FunctionModel` —
no network, no real model.

`FunctionModel`'s streaming form is what actually exercises the code this
module owns: the event translation (`_handle_stream`), the "first text is the
plan" rule, tool dispatch and its UI events (`_run_tool`), and the terminal
`done`/`error` events. Everything else — the turn loop itself, reassembling a
streamed tool call, feeding results back to the model — is pydantic-ai's, and
is not re-tested here.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from ohdp_agent.cube import CubeInfo
from ohdp_agent.literature import LiteratureClient
from ohdp_agent.loop import (
    ASK_USER_TOOLSET,
    CUBE_TOOLSET,
    LITERATURE_TOOLSET,
    AskRegistry,
    AskUserChannel,
    Deps,
    Turn,
    _extract_followups,
    build_agent,
    resolve_ask,
    run,
)


class FakeCube:
    async def list_metrics(self) -> list[CubeInfo]:
        return []


def _agent(stream_function: Any) -> Agent[Deps, str]:
    model = FunctionModel(stream_function=stream_function)
    return Agent(model, deps_type=Deps, toolsets=[CUBE_TOOLSET, LITERATURE_TOOLSET])


async def _run(agent: Agent[Deps, str]) -> tuple[Turn, list[Any]]:
    turn = Turn(question="what metrics exist?", persona="")
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await run(
        agent,
        question=turn.question,
        persona="",
        turn=turn,
        queue=queue,
        cube=FakeCube(),  # type: ignore[arg-type]
        literature=LiteratureClient(),
        catalog=None,
    )
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return turn, events


async def test_follow_ups_block_is_split_off_the_answer_and_suggested() -> None:
    """The `<<<FOLLOW-UPS>>>` block the model was told to append becomes the
    `done` event's `suggestions`, and never appears in the answer itself."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        yield (
            "The worst city was Springfield.\n\n"
            "<<<FOLLOW-UPS>>>\n"
            '["What about ozone levels?", "Show the trend over time.", '
            '"Which cities improved?"]'
        )

    turn, events = await _run(_agent(stream_function))

    assert turn.answer == "The worst city was Springfield."
    assert events[-1].type == "done"
    assert events[-1].data["suggestions"] == [
        "What about ozone levels?",
        "Show the trend over time.",
        "Which cities improved?",
    ]
    # The block is cut from turn.timeline's raw text too, not just turn.answer
    # — a reloaded thread renders straight from the timeline
    # (apps/web/src/chat/runtime.ts's turnToMessages), so a leftover sentinel
    # there would leak into the chat the same way a leftover in turn.answer
    # would have.
    assert turn.timeline == [{"type": "text", "text": "The worst city was Springfield."}]


async def test_a_malformed_follow_ups_block_never_leaks_into_the_answer() -> None:
    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        yield "The answer.\n\n<<<FOLLOW-UPS>>>\nnot json at all"

    turn, events = await _run(_agent(stream_function))

    assert turn.answer == "The answer."
    assert events[-1].type == "done"
    assert events[-1].data["suggestions"] == []
    assert "FOLLOW-UPS" not in events[-1].data["answer"]


def test_extract_followups_tolerates_a_fenced_json_block() -> None:
    answer, suggestions = _extract_followups(
        'A.\n\n<<<FOLLOW-UPS>>>\n```json\n["What about B?", "What about C?"]\n```'
    )
    assert answer == "A."
    assert suggestions == ["What about B?", "What about C?"]


def test_extract_followups_without_a_block_changes_nothing() -> None:
    answer, suggestions = _extract_followups("Just an answer.")
    assert answer == "Just an answer."
    assert suggestions == []


async def test_plan_tool_call_and_final_answer_stream_in_order() -> None:
    """One model turn can carry both plan text and a tool call, exactly like a
    real tool-using response often does — pydantic-ai delivers `plan` before
    `tool_call` for such a turn, asserted here rather than assumed.
    """
    calls = 0

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "Here is my plan."
            yield {0: DeltaToolCall(name="list_metrics", json_args="{}", tool_call_id="call_1")}
        else:
            yield "Here is the answer."

    turn, events = await _run(_agent(stream_function))

    kinds = [e.type for e in events]
    # "text" precedes "plan"/the final "done" answer for each turn — a whole
    # part delivered in one piece (as FunctionModel does here) still rides in
    # on PartStartEvent, so the live UI sees it before _handle_stream folds
    # it into the plan/answer.
    assert kinds == ["status", "text", "plan", "tool_call", "tool_result", "text", "done"]
    assert events[1].data["text"] == "Here is my plan."
    assert events[5].data["text"] == "Here is the answer."
    assert turn.plan == "Here is my plan."
    # The answer is every turn's text joined, not just the final one — the
    # plan is part of what the reader is shown as the answer too (rule 2 and
    # rule 4 of the system prompt).
    assert turn.answer == "Here is my plan.\n\nHere is the answer."
    assert events[-1].data["answer"] == turn.answer

    tool_call_event, tool_result_event = events[3], events[4]
    # Both share the id the model gave the call — what lets the frontend
    # attach the result (and, for render_dashboard's HTML, an embedded chart)
    # to the call it belongs to (apps/web/src/chat/runtime.ts).
    assert tool_call_event.data["tool_call_id"] == "call_1"
    assert tool_result_event.data["tool_call_id"] == "call_1"
    assert tool_result_event.data["is_error"] is False
    assert "format" not in tool_result_event.data
    # turn.tool_calls is what gets persisted to chat_turns.tool_calls and
    # rebuilt into the tool-call/tool-result bubble on reload
    # (apps/web/src/chat/runtime.ts's turnToMessages) — the call and its
    # result must already be folded into one dict, since a reloaded thread
    # has no separate SSE events left to pair up.
    assert turn.tool_calls == [
        {
            "tool_call_id": "call_1",
            "name": "list_metrics",
            "input": {},
            "result": tool_result_event.data["result"],
            "is_error": False,
        }
    ]
    # turn.timeline is what lets a reloaded thread put the tool-call chip
    # between the plan and the final answer, instead of grouping every call
    # before the answer text (apps/web/src/chat/runtime.ts's turnToMessages).
    assert turn.timeline == [
        {"type": "text", "text": "Here is my plan."},
        {"type": "tool_call", "tool_call_id": "call_1"},
        {"type": "text", "text": "Here is the answer."},
    ]


async def test_follow_ups_are_trimmed_off_the_timeline_after_a_tool_call() -> None:
    """The follow-ups block lands in the *last* of several text segments this
    turn produced (plan, then tool call, then final answer) — `_trim_timeline_tail`
    has to walk back across that segment boundary correctly, not just trim
    whatever the single most-recent segment happens to be."""
    calls = 0

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "Here is my plan."
            yield {0: DeltaToolCall(name="list_metrics", json_args="{}", tool_call_id="call_1")}
        else:
            yield (
                "Here is the answer.\n\n"
                "<<<FOLLOW-UPS>>>\n"
                '["What about ozone levels?", "Show the trend.", "Which improved?"]'
            )

    turn, _events = await _run(_agent(stream_function))

    assert turn.answer == "Here is my plan.\n\nHere is the answer."
    assert turn.timeline == [
        {"type": "text", "text": "Here is my plan."},
        {"type": "tool_call", "tool_call_id": "call_1"},
        {"type": "text", "text": "Here is the answer."},
    ]


async def test_a_rejected_query_is_a_tool_error_the_model_can_recover_from() -> None:
    calls = 0

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            # `sql` is not a real property of run_metric_query — CubeQuery(**kwargs)
            # rejects it, which is the ValidationError -> ModelRetry path.
            yield {
                0: DeltaToolCall(
                    name="run_metric_query", json_args='{"sql": "select 1"}', tool_call_id="call_1"
                )
            }
        else:
            yield "I could not run that query."

    turn, events = await _run(_agent(stream_function))

    assert calls == 2  # the model got a second turn after the rejection
    tool_errors = [e for e in events if e.type == "tool_error"]
    assert len(tool_errors) == 1
    assert "list_metrics" not in tool_errors[0].data["message"]
    assert events[-1].type == "done"
    assert turn.answer == "I could not run that query."

    # A ModelRetry (here, from the rejected query) becomes a RetryPromptPart,
    # which _tool_result_payload always reports as an error — there is no
    # `outcome` field on that part to read instead (ohdp_agent.loop).
    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].data["tool_call_id"] == "call_1"
    assert tool_results[0].data["is_error"] is True


async def test_gives_up_after_too_many_steps() -> None:
    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        # Always asks for another tool call — never produces a final answer.
        yield {0: DeltaToolCall(name="list_metrics", json_args="{}", tool_call_id="call_x")}

    turn, events = await _run(_agent(stream_function))

    assert events[-1].type == "error"
    assert "too many steps" in events[-1].data["message"]
    assert turn.answer == ""
    assert "too many steps" in turn.error


async def test_cancellation_persists_partial_answer() -> None:
    """If the user (or client) cancels mid-stream, the accumulated text is
    flushed to the turn so the UI can render the latest state instead of an
    empty message."""

    async def empty_stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        if False:
            yield ""

    agent = _agent(empty_stream)
    turn = Turn(question="what metrics exist?", persona="")
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def fake_run(*args: Any, **kwargs: Any) -> Any:
        # Simulate the stream having produced text before cancellation.
        kwargs["deps"].answer_parts.append("Partial answer so far")
        raise asyncio.CancelledError()

    agent.run = fake_run  # type: ignore[method-assign]

    with contextlib.suppress(asyncio.CancelledError):
        await run(
            agent,
            question=turn.question,
            persona="",
            turn=turn,
            queue=queue,
            cube=FakeCube(),  # type: ignore[arg-type]
            literature=LiteratureClient(),
            catalog=None,
        )

    assert turn.answer == "Partial answer so far"
    assert turn.error == "Generation was cancelled."


def test_build_agent_picks_the_backend_from_base_url() -> None:
    from pydantic_ai.models.openai import OpenAIChatModel

    openai_agent = build_agent(
        model_id="openai/gpt-4o-mini", base_url="https://openrouter.ai/api/v1", api_key="sk-or-test"
    )
    assert isinstance(openai_agent.model, OpenAIChatModel)


# ---------------------------------------------------------------------------
# ask_user — the one tool whose result comes from the reader (docs/chatbot.md §4b)
# ---------------------------------------------------------------------------


def _ask_agent(stream_function: Any) -> Agent[Deps, str]:
    return Agent(
        FunctionModel(stream_function=stream_function),
        deps_type=Deps,
        toolsets=[ASK_USER_TOOLSET],
    )


async def _run_with_ask(
    agent: Agent[Deps, str], ask: AskUserChannel | None
) -> tuple[Turn, list[Any], asyncio.Queue[Any]]:
    turn = Turn(question="which metric did you mean?", persona="")
    queue: asyncio.Queue[Any] = asyncio.Queue()
    await run(
        agent,
        question=turn.question,
        persona="",
        turn=turn,
        queue=queue,
        cube=FakeCube(),  # type: ignore[arg-type]
        literature=LiteratureClient(),
        catalog=None,
        ask=ask,
    )
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return turn, events, queue


def _asks_once(options: str) -> Any:
    """A model that calls ask_user on its first turn and answers on its second."""
    calls = 0

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="ask_user",
                    json_args='{"question": "Which one?", "options": ' + options + "}",
                    tool_call_id="call_ask",
                )
            }
        else:
            # The chosen option came back as this tool call's result, so the
            # answer can quote it — that round trip is the whole point.
            last = messages[-1].parts[-1]
            yield f"You picked: {getattr(last, 'content', '')}"

    return stream_function


async def test_ask_user_blocks_until_the_reader_answers() -> None:
    """The tool emits an `ask_user` event, waits, and returns the reader's
    choice to the model as an ordinary tool result."""
    pending: AskRegistry = {}
    channel = AskUserChannel(owner="researcher@example.org", pending=pending)
    agent = _ask_agent(_asks_once('["Adults 18+", "Ages 35+"]'))

    task = asyncio.create_task(_run_with_ask(agent, channel))

    # The run must still be blocked on the question — an `ask_user` that
    # returned before anyone answered would defeat the entire design.
    ask_id = await _await_ask_id(pending)
    assert not task.done()

    assert resolve_ask(pending, ask_id=ask_id, owner="researcher@example.org", answer="Ages 35+")
    turn, events, _queue = await task

    ask_events = [e for e in events if e.type == "ask_user"]
    assert len(ask_events) == 1
    assert ask_events[0].data["question"] == "Which one?"
    assert ask_events[0].data["options"] == ["Adults 18+", "Ages 35+"]
    assert ask_events[0].data["tool_call_id"] == "call_ask"
    assert ask_events[0].data["allow_other"] is False
    assert turn.answer == "You picked: Ages 35+"
    # The registry entry is gone once answered, so a second click 404s rather
    # than resolving an already-finished future.
    assert pending == {}


async def _await_ask_id(pending: AskRegistry) -> str:
    """Wait for the run to register its question, without a fixed sleep."""
    for _ in range(200):
        if pending:
            return next(iter(pending))
        await asyncio.sleep(0.01)
    raise AssertionError("the run never registered an ask_user question")


async def test_ask_user_rejects_another_users_answer() -> None:
    pending: AskRegistry = {}
    channel = AskUserChannel(owner="researcher@example.org", pending=pending)
    agent = _ask_agent(_asks_once('["Adults 18+", "Ages 35+"]'))

    task = asyncio.create_task(_run_with_ask(agent, channel))
    ask_id = await _await_ask_id(pending)

    assert not resolve_ask(pending, ask_id=ask_id, owner="someone@else.org", answer="Ages 35+")
    assert not task.done()

    assert resolve_ask(pending, ask_id=ask_id, owner="researcher@example.org", answer="Adults 18+")
    turn, _events, _queue = await task
    assert turn.answer == "You picked: Adults 18+"


async def test_ask_user_times_out_into_a_keep_going_result() -> None:
    """Nobody answers: the tool comes back with an instruction to continue,
    not an error and not a retry — asking again is the wrong response to an
    empty room."""
    pending: AskRegistry = {}
    channel = AskUserChannel(owner="researcher@example.org", pending=pending, timeout=0.05)
    agent = _ask_agent(_asks_once('["Adults 18+", "Ages 35+"]'))

    turn, events, _queue = await _run_with_ask(agent, channel)

    assert "No answer" in turn.answer
    assert [e.type for e in events if e.type == "ask_cancelled"] == ["ask_cancelled"]
    assert pending == {}


async def test_ask_user_without_a_channel_tells_the_model_to_answer_anyway() -> None:
    """A surface with no reader attached (an eval harness) still builds the
    agent with the toolset — `loop.run` just gives it nowhere to ask."""
    agent = _ask_agent(_asks_once('["Adults 18+", "Ages 35+"]'))

    turn, events, _queue = await _run_with_ask(agent, None)

    assert [e for e in events if e.type == "ask_user"] == []
    assert "no one to ask" in turn.answer.lower()


async def test_ask_user_needs_two_distinct_options() -> None:
    """One option is not a choice. The model is told so and gets to fix it."""
    calls = 0

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="ask_user",
                    json_args='{"question": "Which one?", "options": ["Ages 35+", "Ages 35+"]}',
                    tool_call_id="call_ask",
                )
            }
        else:
            yield "Assuming ages 35+."

    pending: AskRegistry = {}
    channel = AskUserChannel(owner="researcher@example.org", pending=pending)
    turn, events, _queue = await _run_with_ask(_ask_agent(stream_function), channel)

    assert calls == 2  # the rejection reached the model as a retry
    assert [e for e in events if e.type == "ask_user"] == []
    assert turn.answer == "Assuming ages 35+."
