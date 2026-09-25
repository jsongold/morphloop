"""The Assistant's tool loop: one reply to one learner message (#34, #63).

Context is minimal: the pack/wiring system prompt, the thread's target and
labels, and the thread's messages. The model may call registered tools
(:mod:`harness.core.chat.tools`); each call's arguments are validated against
the tool's schema before it runs. In a ``mode:hint`` thread every ``expected``
key (a drill item's correct answer) is stripped from the target and from every
tool result before the model sees it, the same idea as v0.1's
``assert_no_reference_solution``: the answer never enters the context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from harness.core.chat.tools import ToolContext, offered_tools
from harness.core.ports.json_types import JsonObject, JsonValue, PlainJson, to_plain_json
from harness.core.ports.llm import (
    LLMMessage,
    LLMProvenance,
    LLMToolProvider,
    LLMToolRequest,
    LLMToolResult,
    MessageRole,
)

HINT_LABEL = "mode:hint"
EXPECTED_KEY = "expected"


class AssistantError(Exception):
    """The model produced no usable reply; no ``chat.replied`` is recorded."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistantConfig:
    """Supplied by wiring (the harness holds no defaults, ADR-0002)."""

    llm: LLMProvenance
    system_prompt: str
    max_tool_rounds: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Reply:
    text: str
    tool_calls: list[PlainJson]
    llm: LLMProvenance


def without_expected(value: JsonValue) -> PlainJson:
    """``value`` with every ``expected`` key removed, at any depth."""
    if isinstance(value, Mapping):
        return {k: without_expected(v) for k, v in value.items() if k != EXPECTED_KEY}
    if isinstance(value, list | tuple):
        return [without_expected(v) for v in value]
    return to_plain_json(value)


def reply(
    provider: LLMToolProvider,
    config: AssistantConfig,
    ctx: ToolContext,
    history: Sequence[JsonObject],
    *,
    allow_writes: bool,
) -> Reply:
    """Run the tool loop until the model answers with text and no tool calls."""
    hint = HINT_LABEL in ctx.labels
    guard = without_expected if hint else to_plain_json
    thread = {"target": ctx.target, "labels": list(ctx.labels)}
    messages: list[LLMMessage | LLMToolResult] = [
        LLMMessage(role="system", content=config.system_prompt),
        LLMMessage(role="system", content=json.dumps({"thread": guard(thread)})),
    ]
    for m in history:
        role: MessageRole = "user" if m["role"] == "learner" else "assistant"
        messages.append(LLMMessage(role=role, content=str(m["text"])))
    tools = offered_tools(allow_writes=allow_writes)
    specs = [tool.spec() for tool in tools.values()]
    calls: list[PlainJson] = []
    for _ in range(config.max_tool_rounds + 1):
        response = provider.complete_with_tools(
            LLMToolRequest(llm=config.llm, messages=messages, tools=specs, tool_choice="auto")
        )
        if not response.tool_calls:
            if not response.content:
                raise AssistantError("the model returned neither text nor tool calls")
            return Reply(text=response.content, tool_calls=calls, llm=response.provenance)
        messages.append(
            LLMMessage(
                role="assistant", content=response.content or "", tool_calls=response.tool_calls
            )
        )
        for call in response.tool_calls:
            tool = tools.get(call.name)
            if tool is None:
                raise AssistantError(f"the model called a tool it was not offered: {call.name!r}")
            errors = [
                e.message for e in Draft202012Validator(tool.parameters).iter_errors(call.arguments)
            ]
            if errors:
                raise AssistantError(f"invalid arguments for {call.name!r}: {errors}")
            result = guard(tool.run(call.arguments, ctx))
            messages.append(LLMToolResult(tool_call_id=call.id, content=json.dumps(result)))
            calls.append({"name": call.name, "arguments": to_plain_json(call.arguments)})
    raise AssistantError(f"no text reply within {config.max_tool_rounds} tool rounds")
