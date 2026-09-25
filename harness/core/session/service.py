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

from collections.abc import Sequence

from harness.core.pack.v2.importer import PackV2
from harness.core.ports import JsonObject
from harness.core.ports.events_v2 import EventTransactionV2, EventV2
from harness.core.session.model import SessionView, TopicNotFoundError, find_topic
from harness.core.view import dispatch

__all__ = [
    "PackMismatchError",
    "TopicNotFoundError",
    "create_session",
    "get_session",
    "list_sessions",
    "session_id_for",
]


class PackMismatchError(LookupError):
    """``pack_id`` does not name the pack currently loaded by the server."""


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
    its topic tree -- both before any event is appended. A reused
    ``event_id`` with a different ``pack_id``/``topic_id`` raises
    :class:`~harness.core.ports.events_v2.EventIdConflictError` from
    ``tx.append`` itself.
    """
    if pack_id != pack.pack_id:
        raise PackMismatchError(pack_id)
    topic = find_topic(pack.topics, topic_id)
    session_id = session_id_for(event_id)
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


def get_session(tx: EventTransactionV2, session_id: str) -> JsonObject | None:
    """The session document at ``session_id``, or ``None``."""
    return SessionView.get(tx, session_id)


def list_sessions(tx: EventTransactionV2) -> Sequence[JsonObject]:
    """Every session document, in key (creation) order."""
    return [doc for _key, doc in SessionView.list(tx)]
