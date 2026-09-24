"""Tool calling through the litellm adapter. No network: a fake completion
returns real ``litellm.types.utils.ModelResponse`` objects."""

from __future__ import annotations

import json
from typing import Any

import pytest
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    Message,
    ModelResponse,
)

from harness.adapters.litellm import LiteLLMProvider
from harness.core.ports import LLMMessage, LLMOutputError, LLMProvenance
from harness.core.ports.llm import (
    LLMTool,
    LLMToolCall,
    LLMToolProvider,
    LLMToolRequest,
    LLMToolResult,
)

LLM = LLMProvenance(
    provider="openai",
    model="openai/gpt-5",
    prompt_id="assistant",
    prompt_version="1",
    generation_parameters={"temperature": 0},
)
TOOL = LLMTool(
    name="memo_append",
    description="Append a memo entry.",
    parameters={
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
        "additionalProperties": False,
    },
)


def _request(**overrides: Any) -> LLMToolRequest:
    fields: dict[str, Any] = {
        "llm": LLM,
        "messages": [LLMMessage(role="user", content="note: dig uses UDP")],
        "tools": [TOOL],
        "tool_choice": "auto",
    }
    fields.update(overrides)
    return LLMToolRequest(**fields)


def _reply(content: str | None, *calls: tuple[str, str, str]) -> ModelResponse:
    tool_calls = [
        ChatCompletionMessageToolCall(id=i, function=Function(name=n, arguments=a))
        for i, n, a in calls
    ] or None
    message = Message(content=content, role="assistant", tool_calls=tool_calls)
    return ModelResponse(
        choices=[Choices(finish_reason="tool_calls", index=0, message=message)],
        model="gpt-5-2025-08-07",
    )


class _Fake:
    def __init__(self, reply: ModelResponse) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.reply


def test_sends_tools_and_parses_tool_calls() -> None:
    fake = _Fake(_reply(None, ("call_1", "memo_append", '{"text": "dig uses UDP"}')))
    provider: LLMToolProvider = LiteLLMProvider(completion=fake)

    response = provider.complete_with_tools(_request())

    sent = fake.calls[0]
    assert sent["tool_choice"] == "auto"
    assert sent["temperature"] == 0
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "memo_append",
                "description": "Append a memo entry.",
                "parameters": TOOL.parameters,
            },
        }
    ]
    assert "response_format" not in sent
    assert response.content is None
    assert response.tool_calls == [
        LLMToolCall(id="call_1", name="memo_append", arguments={"text": "dig uses UDP"})
    ]
    assert response.provenance.model == "gpt-5-2025-08-07"
    assert response.provenance.provider == "openai"


def test_text_reply_has_no_tool_calls() -> None:
    response = LiteLLMProvider(completion=_Fake(_reply("Noted."))).complete_with_tools(_request())
    assert response.content == "Noted."
    assert response.tool_calls == []


def test_replays_assistant_calls_and_tool_results() -> None:
    fake = _Fake(_reply("Saved."))
    call = LLMToolCall(id="call_1", name="memo_append", arguments={"text": "x"})
    messages = [
        LLMMessage(role="user", content="note x"),
        LLMMessage(role="assistant", content="", tool_calls=[call]),
        LLMToolResult(tool_call_id="call_1", content='{"entry_id": "e1"}'),
    ]
    LiteLLMProvider(completion=fake).complete_with_tools(_request(messages=messages))

    wire = fake.calls[0]["messages"]
    assert wire[1]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "memo_append", "arguments": json.dumps({"text": "x"})},
        }
    ]
    assert wire[2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"entry_id": "e1"}'}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("rm_rf", "{}"),
        ("memo_append", "not json"),
        ("memo_append", "[1]"),
        ("memo_append", '{"text": 3}'),
    ],
)
def test_bad_tool_calls_raise_output_error(name: str, arguments: str) -> None:
    provider = LiteLLMProvider(completion=_Fake(_reply(None, ("c", name, arguments))))
    with pytest.raises(LLMOutputError):
        provider.complete_with_tools(_request())
