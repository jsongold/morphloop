"""Invariants of the tool-calling value types in the LLM Port."""

from __future__ import annotations

import pytest

from harness.core.ports import LLMMessage, LLMProvenance
from harness.core.ports.llm import LLMTool, LLMToolCall, LLMToolRequest, LLMToolResult

LLM = LLMProvenance(
    provider="openai",
    model="openai/gpt-5",
    prompt_id="assistant",
    prompt_version="1",
    generation_parameters={},
)
TOOL = LLMTool(
    name="memo_append", description="Append a memo entry.", parameters={"type": "object"}
)


def test_plain_message_stays_backward_compatible() -> None:
    assert LLMMessage(role="user", content="hi").tool_calls == ()


def test_only_assistant_messages_carry_tool_calls() -> None:
    call = LLMToolCall(id="c1", name="memo_append", arguments={})
    assert LLMMessage(role="assistant", content="", tool_calls=[call]).tool_calls == [call]
    with pytest.raises(ValueError, match="assistant"):
        LLMMessage(role="user", content="", tool_calls=[call])


def test_tool_call_and_result_need_ids() -> None:
    with pytest.raises(ValueError):
        LLMToolCall(id="", name="memo_append", arguments={})
    with pytest.raises(ValueError):
        LLMToolResult(tool_call_id="", content="ok")


def test_request_needs_messages_and_unique_tools() -> None:
    messages = [LLMMessage(role="user", content="note this")]
    LLMToolRequest(llm=LLM, messages=messages, tools=[TOOL], tool_choice="auto")
    with pytest.raises(ValueError, match="message"):
        LLMToolRequest(llm=LLM, messages=[], tools=[TOOL], tool_choice="auto")
    with pytest.raises(ValueError, match="tool"):
        LLMToolRequest(llm=LLM, messages=messages, tools=[], tool_choice="auto")
    with pytest.raises(ValueError, match="unique"):
        LLMToolRequest(llm=LLM, messages=messages, tools=[TOOL, TOOL], tool_choice="auto")
