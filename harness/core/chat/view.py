"""``chat.messages`` / ``chat.thread`` views (#63, #104)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import (
    JsonObject,
    JsonValue,
    PlainJson,
    format_timestamp,
    to_plain_json,
)
from harness.core.view import View

SENT = "chat.sent"
REPLIED = "chat.replied"
THREAD_CREATED = "thread.created"


def _key(ws_id: str, thread_id: str) -> str:
    return f"{ws_id}/{thread_id}"


class ChatMessagesView(View):
    """Key ``<ws_id>/<thread_id>``; document ``{"messages": [message, ...]}``."""

    name = "chat.messages"
    handles: ClassVar[frozenset[str]] = frozenset({SENT, REPLIED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        p = event.payload
        key = _key(str(event.ws_id), str(p["thread_id"]))
        message: dict[str, PlainJson] = {
            "message_id": str(p["message_id"]),
            "role": event.actor,
            "text": str(p["text"]),
            "created_at": format_timestamp(event.created_at),
        }
        if event.type == REPLIED:
            message["in_reply_to"] = str(p["in_reply_to"])
            message["tool_calls"] = to_plain_json(p["tool_calls"])
        messages = [to_plain_json(m) for m in cls._stored(tx, key)]
        tx.put_view(cls.name, key, {"messages": [*messages, message]})

    @classmethod
    def messages(cls, tx: ViewDocumentStore, ws_id: str, thread_id: str) -> Sequence[JsonObject]:
        return [m for m in cls._stored(tx, _key(ws_id, thread_id)) if isinstance(m, Mapping)]

    @classmethod
    def _stored(cls, tx: ViewDocumentStore, key: str) -> Sequence[JsonValue]:
        doc = cls.get(tx, key)
        messages = doc.get("messages") if doc else None
        return messages if isinstance(messages, list) else []


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
