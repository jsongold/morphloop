"""Chat send / reply / tool loop against a fake LLM (#63)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from chat.chat_fakes import (
    CONFIG,
    THREAD,
    USER,
    WS,
    FakeToolProvider,
    call,
    store_with_thread,
    text,
)

from harness.core.chat import (
    AssistantError,
    ChatTool,
    ThreadNotFoundError,
    ToolContext,
    list_messages,
    offered_tools,
    send_message,
)
from harness.core.ports.events_v2 import EventStoreV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMError, LLMMessage, LLMToolResult


class _WriteProbe(ChatTool):
    name = "test_write_probe"
    description = "test-only write tool"
    parameters: ClassVar[JsonObject] = {"type": "object"}
    writes = True

    @classmethod
    def run(cls, arguments: JsonObject, ctx: ToolContext) -> JsonObject:
        return {}


def _send(store: EventStoreV2, llm: FakeToolProvider, event_id: str | None = None, **kw: bool):
    return send_message(
        store,
        llm,
        CONFIG,
        user_id=USER,
        ws_id=WS,
        thread_id=THREAD,
        event_id=event_id or str(uuid.uuid4()),
        text="what is this about?",
        allow_writes=kw.get("allow_writes", False),
    )


def test_send_runs_a_tool_then_replies_and_records_both(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path, target={"kind": "memo_entry", "entry_id": "e1"})
    llm = FakeToolProvider([call("thread_target"), text("It is about memo entry e1.")])

    result = _send(store, llm)

    assert result.sent["role"] == "learner"
    assert result.reply["text"] == "It is about memo entry e1."
    assert result.reply["tool_calls"] == [{"name": "thread_target", "arguments": {}}]
    tool_result = llm.requests[1].messages[-1]
    assert isinstance(tool_result, LLMToolResult)
    assert json.loads(tool_result.content)["target"] == {"kind": "memo_entry", "entry_id": "e1"}
    types = [e.type for e in store.read(ws_id=WS)]
    assert types == ["ws.created", "thread.created", "chat.sent", "chat.replied"]
    assert list_messages(store, USER, WS, THREAD) == [result.sent, result.reply]


def test_context_is_prompt_thread_and_messages_only(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    llm = FakeToolProvider([text("a"), text("b")])
    _send(store, llm)
    _send(store, llm)
    roles = [m.role for m in llm.requests[1].messages if isinstance(m, LLMMessage)]
    assert roles == ["system", "system", "user", "assistant", "user"]


def test_hint_thread_never_shows_expected(tmp_path: Path) -> None:
    target = {"kind": "drill_item", "item": {"question": "q?", "expected": "SECRET"}}
    store = store_with_thread(tmp_path, target=target, labels=["mode:hint"])
    llm = FakeToolProvider([call("thread_target"), text("think about q")])
    _send(store, llm)
    sent = "".join(m.content for r in llm.requests for m in r.messages)
    assert "SECRET" not in sent
    assert "q?" in sent


def test_write_tools_are_offered_only_on_command() -> None:
    assert "test_write_probe" not in offered_tools(allow_writes=False)
    assert "test_write_probe" in offered_tools(allow_writes=True)
    assert "thread_target" in offered_tools(allow_writes=False)


def test_unoffered_tool_call_fails_without_a_reply(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    with pytest.raises(AssistantError):
        _send(store, FakeToolProvider([call("test_write_probe")]))
    assert [e.type for e in store.read(ws_id=WS)][-1] == "chat.sent"


def test_invalid_tool_arguments_are_rejected(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    with pytest.raises(AssistantError, match="invalid arguments"):
        _send(store, FakeToolProvider([call("thread_target", {"x": 1})]))


def test_tool_rounds_are_bounded(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    llm = FakeToolProvider([call("thread_target")] * 3)
    with pytest.raises(AssistantError, match="tool rounds"):
        _send(store, llm)


def test_resend_returns_stored_reply_or_retries_after_failure(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    event_id = str(uuid.uuid4())
    with pytest.raises(LLMError):
        _send(store, FakeToolProvider([LLMError("down")]), event_id)
    first = _send(store, FakeToolProvider([text("ok")]), event_id)
    again = _send(store, FakeToolProvider([]), event_id)
    assert again == first
    assert [e.type for e in store.read(ws_id=WS)].count("chat.sent") == 1


def test_unknown_thread(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    with pytest.raises(ThreadNotFoundError):
        list_messages(store, USER, WS, "thr_missing")
