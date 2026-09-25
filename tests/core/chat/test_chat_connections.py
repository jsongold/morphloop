"""Chat never opens a second pooled connection per request (#104).

``ConnectionTrackingStore`` models a bounded connection pool: its ``.read()``
fails loudly if it runs while *any* transaction on the same store is open --
the shape of a concurrent request already holding the pool's connection.
Simulating that held transaction here (rather than real concurrent requests)
keeps the test deterministic and single-threaded.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from chat.chat_fakes import CONFIG, THREAD, USER, WS, FakeToolProvider, store_with_thread, text

from harness.core.chat import list_messages, send_message
from harness.testing.fakes_v2 import ConnectionTrackingStore


def test_list_messages_does_not_open_a_second_connection(tmp_path: Path) -> None:
    store = ConnectionTrackingStore(store_with_thread(tmp_path))
    with store.transaction():  # a concurrent request holding the pool's connection
        messages = list_messages(store, USER, WS, THREAD)
    assert messages == []


def test_send_message_does_not_open_a_second_connection(tmp_path: Path) -> None:
    store = ConnectionTrackingStore(store_with_thread(tmp_path))
    llm = FakeToolProvider([text("hello")])
    with store.transaction():  # a concurrent request holding the pool's connection
        result = send_message(
            store,
            llm,
            CONFIG,
            user_id=USER,
            ws_id=WS,
            thread_id=THREAD,
            event_id=str(uuid.uuid4()),
            text="hi",
            allow_writes=False,
        )
    assert result.reply["text"] == "hello"
