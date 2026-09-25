"""The ``ws`` view: one document per workspace (#34, #57).

Other resources need a ws's session without importing this package's service
(ADR-0009: ids are opaque, resolved through events or a view). Once this view
exists, ``WsView.get(tx, ws_id)`` is the canonical way to do that -- callers
that today scan ``ws.created`` events by hand (drill, chat, artifact_lab) can
switch to it (each in its own PR; not done here, ADR-0018 wave rule).
"""

from __future__ import annotations

from collections.abc import Sequence

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject, format_timestamp
from harness.core.view import View

WS_CREATED = "ws.created"
THREAD_CREATED = "thread.created"


class WsView(View):
    """``ws`` view, keyed by ``ws_id``: ``{ws_id, user_id, session_id, labels, created_at}``."""

    name = "ws"
    handles = frozenset({WS_CREATED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None
        labels = event.payload.get("labels") or []
        assert isinstance(labels, Sequence)
        doc: JsonObject = {
            "ws_id": event.ws_id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "labels": [str(label) for label in labels],
            "created_at": format_timestamp(event.created_at),
        }
        tx.put_view(cls.name, event.ws_id, doc)
