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
from typing import ClassVar, cast

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


def _thread_key(ws_id: str, thread_id: str) -> str:
    return f"{ws_id}/{thread_id}"


class ThreadsView(View):
    """Every thread of a ws, keyed ``<ws_id>/<thread_id>`` (#129).

    A second index over the same ``thread.created`` events ``WsView`` already
    handles: ``WsView`` only tracks the ws's *main* thread, and ``thread_id``
    is a uuid-derived id (not creation order), so listing needs its own
    prefix-listable key plus the creating event's ``position`` to sort by
    (same trick as ``HighlightView``, issue #60). Distinct from
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
        tx.put_view(cls.name, _thread_key(event.ws_id, thread_id), doc)

    @classmethod
    def list_for_ws(cls, tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
        """Every thread of ``ws_id``, in creation order (the creating event's
        ``position`` -- never the ``thread_id``/uuid key order)."""
        docs = [doc for _, doc in cls.list(tx, key_prefix=f"{ws_id}/")]
        return sorted(docs, key=lambda doc: cast(int, doc["position"]))
