"""HighlightView: a learner's current highlights, one document per ws (#34, #60).

Documents are keyed ``"<ws_id>:<highlight_id>"`` so
``list_view(key_prefix=f"{ws_id}:")`` returns one ws's highlights.
:class:`~harness.core.ports.events_v2.ViewDocumentStore` has no per-key delete
(a view is rebuilt by replaying every handled event, not edited -- ADR-0008),
so ``highlight.removed`` is a tombstone: the document is overwritten with
``removed: true`` and :func:`active_highlights` filters those out.
"""

from __future__ import annotations

from typing import ClassVar, cast

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
                # Internal only (stripped before the wire, common rule): the
                # highlight_id key is a uuid-derived id, not creation order,
                # so list() sorts by this instead (issue #60 review).
                "position": event.position,
            }
        else:
            doc = {"highlight_id": highlight_id, "ws_id": event.ws_id, "removed": True}
        tx.put_view(cls.name, key, doc)


def active_highlights(tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
    """Current (non-removed) highlights of ``ws_id``, in creation order (the
    creating event's ``position`` -- never the highlight_id/uuid key order)."""
    prefix = f"{ws_id}:"
    docs = [doc for _, doc in HighlightView.list(tx, key_prefix=prefix) if not doc.get("removed")]
    return sorted(docs, key=lambda doc: cast(int, doc["position"]))


def active_highlights_page(
    tx: ViewDocumentStore, ws_id: str, *, after: str | None, limit: int
) -> tuple[list[JsonObject], str | None]:
    """One bounded page of ``ws_id``'s current highlights (#176 keyset
    pagination), in key (highlight_id) order -- unlike :func:`active_highlights`,
    a page never re-sorts by ``position``, since a keyset page's items must
    already be in the store's key order for the cursor to be valid.

    A removed highlight is filtered out of the page's items, but
    ``next_cursor`` still comes straight from the store: a page that is all
    tombstones must not look like the last page just because it filters to
    empty.
    """
    page, next_cursor = HighlightView.list(tx, key_prefix=f"{ws_id}:", after=after, limit=limit)
    return [doc for _, doc in page if not doc.get("removed")], next_cursor
