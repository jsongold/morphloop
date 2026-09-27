"""``chat.messages``: one view document per message, rebuilt from the log (#177)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from chat.chat_fakes import CONFIG, THREAD, USER, WS, FakeToolProvider, store_with_thread, text

from harness.core.chat import ChatMessagesView, list_messages, send_message
from harness.core.chat.view import ChatThreadView
from harness.core.ports.events_v2 import (
    AppendResultV2,
    EventStoreV2,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)


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


class _RecordingTx:
    """Forwards to the real transaction, logging ``lock_view`` and ``append`` calls."""

    def __init__(self, inner: EventTransactionV2, log: list[str]) -> None:
        self._inner = inner
        self._log = log

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def lock_view(self, view: str, key: str) -> None:
        self._log.append(f"lock {view} {key}")
        self._inner.lock_view(view, key)

    def append(self, event: EventV2) -> AppendResultV2:
        self._log.append(f"append {event.type}")
        return self._inner.append(event)


class _RecordingStore:
    def __init__(self, inner: EventStoreV2) -> None:
        self._inner = inner
        self.log: list[str] = []

    @contextmanager
    def transaction(self) -> Iterator[EventTransactionV2]:
        self.log.append("begin")
        with self._inner.transaction() as tx:
            yield cast(EventTransactionV2, _RecordingTx(tx, self.log))

    def read(self, **kwargs: Any) -> Sequence[StoredEventV2]:
        return self._inner.read(**kwargs)


def test_send_locks_the_thread_before_each_append(tmp_path: Path) -> None:
    """#215: both appending transactions lock the ``chat.thread`` document first,
    so concurrent same-thread sends commit in position order."""
    store = _RecordingStore(store_with_thread(tmp_path))
    send_message(
        store,
        FakeToolProvider([text("a1")]),
        CONFIG,
        user_id=USER,
        ws_id=WS,
        thread_id=THREAD,
        event_id=str(uuid.uuid4()),
        text="q1",
        allow_writes=False,
    )
    lock = f"lock {ChatThreadView.name} {THREAD}"
    txs = " | ".join(store.log).split("begin")
    appending = [t for t in txs if "append" in t]
    assert len(appending) == 2
    for t in appending:
        assert t.index(lock) < t.index("append")
