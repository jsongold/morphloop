"""The ``ws`` view: one document per workspace (#34, #57).

Other resources need a ws's session without importing this package's service
(ADR-0009: ids are opaque, resolved through events or a view). ``WsView.get(tx,
ws_id)`` (``ws_or_404`` in the API layer) is the canonical way to do that.

``position`` is the creating event's DB-assigned position, kept so callers
can sort several ws documents by creation order (``list_view`` sorts by the
opaque ``ws_id`` key, which is not creation order).

``main_thread_event_id`` is the id of the event that created the ws's main
(targetless) thread, or ``None`` before one exists. A ws has at most one main
thread (Codex #90): the first targetless ``thread.created`` claims it, and
``create_thread`` (``service.py``) returns that thread again for any later
targetless request instead of creating a second one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject, format_timestamp, to_plain_json
from harness.core.view import View

WS_CREATED = "ws.created"
THREAD_CREATED = "thread.created"


class WsView(View):
    """``ws`` view, keyed by ``ws_id``.

    ``{ws_id, user_id, session_id, labels, created_at, position, main_thread_event_id}``.
    """

    name = "ws"
    handles = frozenset({WS_CREATED, THREAD_CREATED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        if event.type == WS_CREATED:
            cls._apply_ws_created(event, tx)
        else:
            cls._apply_thread_created(event, tx)

    @classmethod
    def _apply_ws_created(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None
        labels = event.payload.get("labels") or []
        assert isinstance(labels, Sequence)
        doc: JsonObject = {
            "ws_id": event.ws_id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "labels": [str(label) for label in labels],
            "created_at": format_timestamp(event.created_at),
            "position": event.position,
            "main_thread_event_id": None,
        }
        tx.put_view(cls.name, event.ws_id, doc)

    @classmethod
    def _apply_thread_created(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        if "target" in event.payload:
            return  # a targeted thread is never the main thread
        assert event.ws_id is not None
        doc = tx.get_view(cls.name, event.ws_id)
        assert doc is not None, "thread.created without its creating ws.created"
        if doc.get("main_thread_event_id") is not None:
            return  # main thread already claimed; keep the first one
        tx.put_view(cls.name, event.ws_id, {**doc, "main_thread_event_id": event.id})


_POSITION_DIGITS = 19  # a Postgres bigint position fits in 19 digits


def position_key(position: int) -> str:
    """``position`` zero-padded, so key order is creation order."""
    return f"{position:0{_POSITION_DIGITS}d}"


def newest_first_key(position: int) -> str:
    """``position`` inverted and zero-padded, so key order is newest first."""
    return position_key(10**_POSITION_DIGITS - 1 - position)


class WsByUserView(View):
    """Per-user ws index (#175), so listing reads one key range, not every ws.

    Keyed ``<user>/<session_id or *>/<newest-first position>``: each ws is
    indexed twice, once under ``*`` (every ws of the user) and once under its
    session, so both listings are one prefix. The document is only the
    ``ws_id``; ``WsView`` stays the one copy of the ws itself.
    """

    name = "ws.by_user"
    handles: ClassVar[frozenset[str]] = frozenset({WS_CREATED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        if event.ws_id is None:
            raise ValueError("ws.created without a ws_id")
        doc: JsonObject = {"ws_id": event.ws_id}
        tail = newest_first_key(event.position)
        tx.put_view(cls.name, f"{cls.prefix(event.user_id, None)}{tail}", doc)
        if event.session_id is not None:
            tx.put_view(cls.name, f"{cls.prefix(event.user_id, event.session_id)}{tail}", doc)

    @classmethod
    def prefix(cls, user_id: str, session_id: str | None) -> str:
        # user/session ids are `usr_`/`ses_` + alphanumerics (contracts ids.json),
        # so the `/` separators are unambiguous and `*` is never a session id.
        return f"{user_id}/{session_id or '*'}/"


class ThreadsView(View):
    """Every thread of a ws, keyed ``<ws_id>/<position>`` (#129, #175).

    A second index over the same ``thread.created`` events ``WsView`` already
    handles: ``WsView`` only tracks the ws's *main* thread, and ``thread_id``
    is a uuid-derived id (not creation order), so the key carries the
    creating event's zero-padded ``position``: key order is creation order,
    and a page is one key range. Distinct from
    ``harness.core.chat.view.ChatThreadView``, which is chat's own
    single-thread lookup (keyed by ``thread_id`` alone) and never imported
    here (ADR-0009).
    """

    name = "ws.threads"
    handles: ClassVar[frozenset[str]] = frozenset({THREAD_CREATED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None
        thread_id = str(event.payload["thread_id"])
        target = event.payload.get("target")
        labels = event.payload.get("labels")
        doc: JsonObject = {
            "thread_id": thread_id,
            "target": to_plain_json(target) if isinstance(target, Mapping) else None,
            "labels": [str(x) for x in labels] if isinstance(labels, list) else [],
            "created_at": format_timestamp(event.created_at),
            "position": event.position,
        }
        tx.put_view(cls.name, f"{event.ws_id}/{position_key(event.position)}", doc)
