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
            # Not part of the API response (stripped by the route's
            # `_document`): the view key is `ses_<event uuid>`, unrelated to
            # creation order, so `position` is what `SessionsByUserView` keys by
            # (README "Events and views"; #89 review).
            "position": event.position,
            "user_id": event.user_id,
            "created_at": format_timestamp(event.created_at),
            **to_plain_object(event.payload),
        }
        tx.put_view(cls.name, event.session_id, doc)


class SessionsByUserView(View):
    """Per-user session index (#175): a listing reads one key range, not
    every session.

    Keyed ``<user_id>/<zero-padded position>`` (a user id is ``usr_`` +
    alphanumerics, so the ``/`` is unambiguous): key order is creation order.
    The document is only the ``session_id``; ``SessionView`` stays the one
    copy of the session (its pinned tree can be large).
    """

    name = "session.by_user"
    handles: ClassVar[frozenset[str]] = frozenset({"session.created"})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        if event.session_id is None:
            raise ValueError("session.created without a session_id")
        key = f"{cls.prefix(event.user_id)}{event.position:019d}"
        tx.put_view(cls.name, key, {"session_id": event.session_id})

    @classmethod
    def prefix(cls, user_id: str) -> str:
        return f"{user_id}/"
