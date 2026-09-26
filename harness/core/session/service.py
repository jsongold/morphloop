"""Session resource service: create/read on top of ``EventTransactionV2`` (#34, #54).

Only this module (plus ``harness/api/v2/routes/session.py``) knows how
``session.created``, :class:`~harness.core.session.model.SessionView` and the
pack's topic tree fit together; a caller needs only an
:class:`~harness.core.ports.events_v2.EventTransactionV2`, the loaded
:class:`~harness.core.pack.v2.importer.PackV2` and the request fields.

A session's id is derived from its creating event's id (itself the
``Idempotency-Key``), not a fresh random value: this is what makes a resend
with the same key and the same body return the same session, since the
resend then produces an identical event and :class:`SessionView` document
without needing to look anything up first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from harness.core.pack.v2.importer import PackV2
from harness.core.ports import JsonObject, PlainJson, to_plain_json
from harness.core.ports.events_v2 import EventIdConflictError, EventTransactionV2, EventV2
from harness.core.session.model import (
    SessionsByUserView,
    SessionView,
    TopicNotFoundError,
    find_topic,
)
from harness.core.view import dispatch

__all__ = [
    "PackMismatchError",
    "TopicNotFoundError",
    "create_session",
    "get_session",
    "learner_topic_tree",
    "list_sessions",
    "session_id_for",
]


class PackMismatchError(LookupError):
    """``pack_id`` does not name the pack currently loaded by the server."""


def _learner_topic(topic: JsonObject) -> dict[str, PlainJson]:
    visible = {
        key: to_plain_json(topic[key])
        for key in ("id", "title", "description", "labels", "docs")
        if key in topic
    }
    children = topic.get("topics")
    if isinstance(children, Sequence) and not isinstance(children, str | bytes):
        visible["topics"] = [
            _learner_topic(child) for child in children if isinstance(child, Mapping)
        ]
    return visible


def learner_topic_tree(pack: PackV2) -> dict[str, PlainJson]:
    """The loaded pack's topic tree, restricted to learner-visible fields."""
    return {
        "pack_id": pack.pack_id,
        "pack_hash": pack.pack_hash,
        "topics": [_learner_topic(topic) for topic in pack.topics],
    }


def session_id_for(event_id: str) -> str:
    """The session id for the ``session.created`` event ``event_id`` (a UUID).

    Deterministic in ``event_id`` so a resend of the same event -- same id,
    same body -- lands on the same session (``contracts/schemas/common/
    ids.json#/$defs/session_id``).
    """
    return f"ses_{event_id.replace('-', '')}"


def create_session(
    tx: EventTransactionV2,
    *,
    pack: PackV2,
    user_id: str,
    event_id: str,
    pack_id: str,
    topic_id: str,
) -> JsonObject:
    """Append ``session.created`` and return the resulting session document.

    Raises :class:`PackMismatchError` for a ``pack_id`` that is not the
    loaded pack, and :class:`TopicNotFoundError` for a ``topic_id`` outside
    its topic tree -- both before any event is appended.

    Checks a resend of ``event_id`` against the *stored* event first, before
    either check: the currently loaded pack can have moved on (a new
    ``pack_version``/``pack_content_hash``, or a since-edited topic tree)
    since the original request, and re-deriving the payload from today's pack
    would then wrongly 404/409 an identical replay (#89 review). Only the
    client-controlled fields (``user_id``, ``pack_id``, ``topic_id``) decide
    replay vs. conflict; a mismatch raises
    :class:`~harness.core.ports.events_v2.EventIdConflictError`.
    """
    session_id = session_id_for(event_id)
    existing = tx.get(event_id)
    if existing is not None:
        if (
            existing.user_id != user_id
            or existing.payload.get("pack_id") != pack_id
            or existing.payload.get("topic_id") != topic_id
        ):
            raise EventIdConflictError(existing)
        doc = SessionView.get(tx, session_id)
        assert doc is not None
        return doc
    if pack_id != pack.pack_id:
        raise PackMismatchError(pack_id)
    topic = find_topic(pack.topics, topic_id)
    result = tx.append(
        EventV2(
            id=event_id,
            type="session.created",
            actor="learner",
            user_id=user_id,
            session_id=session_id,
            payload={
                "pack_id": pack.pack_id,
                "pack_version": pack.pack_version,
                "pack_content_hash": pack.pack_hash,
                "topic_id": topic_id,
                "tree": topic,
            },
        )
    )
    if result.created:
        dispatch(result.event, tx)
    doc = SessionView.get(tx, session_id)
    assert doc is not None
    return doc


def get_session(tx: EventTransactionV2, session_id: str, *, user_id: str) -> JsonObject | None:
    """The session document at ``session_id`` if it belongs to ``user_id``, else ``None``.

    A session of another user is reported the same as a missing one (#89
    review): the caller does not learn that the id exists.
    """
    doc = SessionView.get(tx, session_id)
    return doc if doc is not None and doc["user_id"] == user_id else None


def list_sessions(
    tx: EventTransactionV2, *, user_id: str, after: str | None = None, limit: int
) -> tuple[list[JsonObject], str | None]:
    """One page of ``user_id``'s sessions in creation order, plus the next
    page's ``after`` key (``None`` on the last page).

    Reads the per-user :class:`SessionsByUserView` key range only (#175); an
    ``after`` outside that range can never reach another user's sessions.
    """
    page, next_key = SessionsByUserView.list(
        tx, key_prefix=SessionsByUserView.prefix(user_id), after=after, limit=limit
    )
    docs = []
    for _key, ref in page:
        doc = SessionView.get(tx, str(ref["session_id"]))
        if doc is None:
            raise LookupError(f"session index names a missing session {ref['session_id']!r}")
        docs.append(doc)
    return docs, next_key
