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
from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from ohdp_agent.cube import CubeInfo
from ohdp_agent.literature import LiteratureClient
from ohdp_agent.loop import BUILTIN_TOOLSET, MODEL, Deps, Turn, build_agent, run


class FakeCube:
    async def list_metrics(self) -> list[CubeInfo]:
        return []


def _agent(stream_function: Any) -> Agent[Deps, str]:
    model = FunctionModel(stream_function=stream_function)
    return Agent(model, deps_type=Deps, toolsets=[BUILTIN_TOOLSET])


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


async def test_plan_tool_call_and_final_answer_stream_in_order() -> None:
    """One model turn can carry both plan text and a tool call, exactly like a
    real Claude response often does — pydantic-ai delivers `plan` before
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
    assert kinds == ["status", "plan", "tool_call", "done"]
    assert turn.plan == "Here is my plan."
    # The answer is every turn's text joined, not just the final one — the
    # plan is part of what the reader is shown as the answer too (rule 2 and
    # rule 4 of the system prompt).
    assert turn.answer == "Here is my plan.\n\nHere is the answer."
    assert turn.tool_calls == [{"name": "list_metrics", "input": {}}]
    assert events[-1].data["answer"] == turn.answer


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


def test_build_agent_picks_the_backend_from_base_url() -> None:
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.openai import OpenAIChatModel

    anthropic_agent = build_agent(model_id=MODEL, api_key="sk-ant-test")
    assert isinstance(anthropic_agent.model, AnthropicModel)

    openai_agent = build_agent(
        model_id="openai/gpt-4o-mini", base_url="https://openrouter.ai/api/v1", api_key="sk-or-test"
    )
    assert isinstance(openai_agent.model, OpenAIChatModel)
