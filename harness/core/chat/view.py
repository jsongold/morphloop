"""``chat.messages`` view: the messages of one thread, in order (#63)."""

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
