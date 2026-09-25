"""HighlightView: a learner's current highlights, one document per ws (#34, #60).

Documents are keyed ``"<ws_id>:<highlight_id>"`` so
``list_view(key_prefix=f"{ws_id}:")`` returns one ws's highlights.
:class:`~harness.core.ports.events_v2.ViewDocumentStore` has no per-key delete
(a view is rebuilt by replaying every handled event, not edited -- ADR-0008),
so ``highlight.removed`` is a tombstone: the document is overwritten with
``removed: true`` and :func:`active_highlights` filters those out.
"""

from __future__ import annotations

from typing import ClassVar

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject, JsonValue, format_timestamp
from harness.core.view import View

CREATED = "highlight.created"
REMOVED = "highlight.removed"


def highlight_key(ws_id: str, highlight_id: str) -> str:
    """The ``ViewDocumentStore`` key for one highlight of one ws."""
    return f"{ws_id}:{highlight_id}"


class HighlightView(View):
    name = "highlight"
    handles: ClassVar[frozenset[str]] = frozenset({CREATED, REMOVED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        highlight_id = str(event.payload["highlight_id"])
        key = highlight_key(str(event.ws_id), highlight_id)
        if event.type == CREATED:
            doc: dict[str, JsonValue] = {
                "highlight_id": highlight_id,
                "ws_id": event.ws_id,
                "anchor": event.payload["anchor"],
                "labels": event.payload.get("labels", []),
                "created_event_id": event.id,
                "created_at": format_timestamp(event.created_at),
                "removed": False,
            }
        else:
            doc = {"highlight_id": highlight_id, "ws_id": event.ws_id, "removed": True}
        tx.put_view(cls.name, key, doc)


def active_highlights(tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
    """Current (non-removed) highlights of ``ws_id``, in key (id) order."""
    prefix = f"{ws_id}:"
    return [doc for _, doc in HighlightView.list(tx, key_prefix=prefix) if not doc.get("removed")]
