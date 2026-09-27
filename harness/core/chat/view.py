"""``chat.messages`` / ``chat.thread`` views (#63, #104)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import (
    JsonObject,
    PlainJson,
    format_timestamp,
    to_plain_json,
)
from harness.core.view import View

SENT = "chat.sent"
REPLIED = "chat.replied"
THREAD_CREATED = "thread.created"


def _prefix(ws_id: str, thread_id: str) -> str:
    return f"{ws_id}/{thread_id}/"


class ChatMessagesView(View):
    """One document per message, keyed ``<ws_id>/<thread_id>/<position:020d>`` (#177).

    The zero-padded event ``position`` makes key order the thread's message
    order, so a thread pages by keyset over its key prefix and each message is
    one write (not a rewrite of the whole thread).
    """

    name = "chat.messages"
    handles: ClassVar[frozenset[str]] = frozenset({SENT, REPLIED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        p = event.payload
        key = f"{_prefix(str(event.ws_id), str(p['thread_id']))}{event.position:020d}"
        message: dict[str, PlainJson] = {
            "message_id": str(p["message_id"]),
            "role": event.actor,
            "text": str(p["text"]),
            "created_at": format_timestamp(event.created_at),
        }
        if event.type == REPLIED:
            message["in_reply_to"] = str(p["in_reply_to"])
            message["tool_calls"] = to_plain_json(p["tool_calls"])
        tx.put_view(cls.name, key, message)

    @classmethod
    def messages(cls, tx: ViewDocumentStore, ws_id: str, thread_id: str) -> Sequence[JsonObject]:
        """Every message of the thread, oldest first."""
        return [doc for _, doc in cls.list(tx, key_prefix=_prefix(ws_id, thread_id))]

    @classmethod
    def page(
        cls, tx: ViewDocumentStore, ws_id: str, thread_id: str, *, after: str | None, limit: int
    ) -> tuple[Sequence[JsonObject], str | None]:
        """One page of the thread's messages, oldest first, plus the next cursor."""
        page, next_cursor = cls.list(
            tx, key_prefix=_prefix(ws_id, thread_id), after=after, limit=limit
        )
        return [doc for _, doc in page], next_cursor


class ChatThreadView(View):
    """``chat.thread`` view, keyed by ``thread_id`` (#104).

    Lets chat resolve a thread's ``ws_id``/``user_id``/``session_id``/``target``/
    ``labels`` by a direct key lookup on the request's transaction, instead of
    scanning the ws's log with ``EventStoreV2.read()`` -- a second pooled
    connection while another request holds a transaction on the same store
    (pool exhaustion under concurrency). Chat's own view: ``ws``'s ``WsView``
    only tracks the ws's *main* (targetless) thread, and chat never imports
    the ``ws`` package (ADR-0009).
    """

    name = "chat.thread"
    handles: ClassVar[frozenset[str]] = frozenset({THREAD_CREATED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        p = event.payload
        target = p.get("target")
        labels = p.get("labels")
        doc: JsonObject = {
            "ws_id": event.ws_id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "target": to_plain_json(target) if isinstance(target, Mapping) else None,
            "labels": [str(x) for x in labels] if isinstance(labels, list) else [],
        }
        tx.put_view(cls.name, str(p["thread_id"]), doc)
