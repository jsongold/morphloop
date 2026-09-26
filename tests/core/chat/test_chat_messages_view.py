"""``chat.messages``: one view document per message, rebuilt from the log (#177)."""

from __future__ import annotations

import uuid
from pathlib import Path

from chat.chat_fakes import CONFIG, THREAD, USER, WS, FakeToolProvider, store_with_thread, text

from harness.core.chat import ChatMessagesView, list_messages, send_message


def test_one_document_per_message_and_rebuild_reproduces_it(tmp_path: Path) -> None:
    store = store_with_thread(tmp_path)
    llm = FakeToolProvider([text("a1"), text("a2")])
    for question in ("q1", "q2"):
        send_message(
            store,
            llm,
            CONFIG,
            user_id=USER,
            ws_id=WS,
            thread_id=THREAD,
            event_id=str(uuid.uuid4()),
            text=question,
            allow_writes=False,
        )
    with store.transaction() as tx:
        docs = list(ChatMessagesView.list(tx))
    positions = [e.position for e in store.read() if e.type in ChatMessagesView.handles]
    assert [key for key, _ in docs] == [f"{WS}/{THREAD}/{p:020d}" for p in positions]
    before = list_messages(store, USER, WS, THREAD)
    assert [m["text"] for m in before] == ["q1", "a1", "q2", "a2"]

    with store.transaction() as tx:
        ChatMessagesView.rebuild(store.read(), tx)
        assert list(ChatMessagesView.list(tx)) == docs
    assert list_messages(store, USER, WS, THREAD) == before
