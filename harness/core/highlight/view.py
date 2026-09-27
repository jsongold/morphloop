"""HighlightView: a learner's current highlights, one document per ws (#34, #60).

Two key shapes live in this one view (so :meth:`View.rebuild` clears and
rebuilds both together):

- ``"<ws_id>:<position:020d>"`` -- the highlight document. ``position`` is the
  creating event's, so ``list_view(key_prefix=f"{ws_id}:")`` returns one ws's
  highlights in creation order, and a keyset page is creation order too
  (#220).
- ``"id:<ws_id>:<highlight_id>"`` -- ``{"position": ...}``, the lookup from a
  ``highlight_id`` (all a ``highlight.removed`` event or a DELETE carries) to
  its document's key. ws ids start with ``ws_``, so no ``f"{ws_id}:"`` prefix
  ever matches these.

:class:`~harness.core.ports.events_v2.ViewDocumentStore` has no per-key delete
(a view is rebuilt by replaying every handled event, not edited -- ADR-0008),
so ``highlight.removed`` is a tombstone: the document is overwritten with
``removed: true`` and the readers below filter those out.
"""

from __future__ import annotations

from typing import ClassVar, cast

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject, JsonValue, format_timestamp
from harness.core.view import View

CREATED = "highlight.created"
REMOVED = "highlight.removed"


def highlight_key(ws_id: str, position: int) -> str:
    """The ``ViewDocumentStore`` key of one highlight document of one ws."""
    return f"{ws_id}:{position:020d}"


def highlight_id_key(ws_id: str, highlight_id: str) -> str:
    """The key of the ``highlight_id`` -> document-key lookup."""
    return f"id:{ws_id}:{highlight_id}"


class HighlightView(View):
    name = "highlight"
    handles: ClassVar[frozenset[str]] = frozenset({CREATED, REMOVED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        ws_id = str(event.ws_id)
        highlight_id = str(event.payload["highlight_id"])
        if event.type == CREATED:
            position = event.position
            tx.put_view(cls.name, highlight_id_key(ws_id, highlight_id), {"position": position})
            doc: dict[str, JsonValue] = {
                "highlight_id": highlight_id,
                "ws_id": event.ws_id,
                "anchor": event.payload["anchor"],
                "labels": event.payload.get("labels", []),
                "created_event_id": event.id,
                "created_at": format_timestamp(event.created_at),
                "removed": False,
                # Internal only (stripped before the wire, common rule).
                "position": position,
            }
        else:
            lookup = cls.get(tx, highlight_id_key(ws_id, highlight_id))
            if lookup is None:  # a removal is only appended for an active highlight
                return
            position = cast(int, lookup["position"])
            doc = {
                "highlight_id": highlight_id,
                "ws_id": event.ws_id,
                "removed": True,
                "position": position,
            }
        tx.put_view(cls.name, highlight_key(ws_id, position), doc)


def get_highlight(tx: ViewDocumentStore, ws_id: str, highlight_id: str) -> JsonObject | None:
    """The document (possibly a tombstone) of ``highlight_id`` in ``ws_id``."""
    lookup = HighlightView.get(tx, highlight_id_key(ws_id, highlight_id))
    if lookup is None:
        return None
    return HighlightView.get(tx, highlight_key(ws_id, cast(int, lookup["position"])))


def active_highlights(tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
    """Current (non-removed) highlights of ``ws_id``, in creation order (key order)."""
    return [
        doc for _, doc in HighlightView.list(tx, key_prefix=f"{ws_id}:") if not doc.get("removed")
    ]


def active_highlights_page(
    tx: ViewDocumentStore, ws_id: str, *, after: str | None, limit: int
) -> tuple[list[JsonObject], str | None]:
    """One bounded page of ``ws_id``'s current highlights (#176 keyset
    pagination), in creation order -- the same order as
    :func:`active_highlights`, since the key is the creating event's position.

    A removed highlight is filtered out of the page's items, but
    ``next_cursor`` still comes straight from the store: a page that is all
    tombstones must not look like the last page just because it filters to
    empty.
    """
    page, next_cursor = HighlightView.list(tx, key_prefix=f"{ws_id}:", after=after, limit=limit)
    return [doc for _, doc in page if not doc.get("removed")], next_cursor
