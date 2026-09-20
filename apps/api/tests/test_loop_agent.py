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
    CUBE_TOOLSET,
    LITERATURE_TOOLSET,
    Deps,
    Turn,
    _extract_followups,
    build_agent,
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
