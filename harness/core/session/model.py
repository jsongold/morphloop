"""Topic subtree lookup and the session View (#34, #54).

A session pins a *copy* of the pack's topic subtree at creation (ADR-0018):
the chosen ``topic_id`` node, found anywhere in :attr:`PackV2.topics`,
together with every descendant. The pack itself may move on; a session's
``tree`` never changes (it is not re-read from the pack).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from harness.core.ports import JsonObject, PlainJson, format_timestamp, to_plain_object
from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.view import View


class TopicNotFoundError(LookupError):
    """No topic with the given id exists in the pack's topic tree."""


def find_topic(topics: Sequence[JsonObject], topic_id: str) -> JsonObject:
    """The topic node (with its descendants) whose ``id`` is ``topic_id``.

    Searches ``topics`` and every nested ``topics`` child, depth first.
    Raises :class:`TopicNotFoundError` if no topic matches.
    """
    stack = list(reversed(topics))
    while stack:
        topic = stack.pop()
        if topic["id"] == topic_id:
            return topic
        children = topic.get("topics", ())
        assert isinstance(children, Sequence)
        stack.extend(child for child in reversed(children) if isinstance(child, Mapping))
    raise TopicNotFoundError(topic_id)


class SessionView(View):
    """Sessions by id (``contracts/openapi/v0.2/paths/session.yaml``)."""

    name = "session"
    handles: ClassVar[frozenset[str]] = frozenset({"session.created"})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.session_id is not None
        doc: dict[str, PlainJson] = {
            "id": event.session_id,
            "user_id": event.user_id,
            "created_at": format_timestamp(event.created_at),
            **to_plain_object(event.payload),
        }
        tx.put_view(cls.name, event.session_id, doc)
