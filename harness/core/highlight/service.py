"""Highlight create/remove/list over the v0.2 event log (#34, #60).

Framework-free orchestration: appends the event, dispatches it to every
registered ``View`` (ADR-0008: the append and every view update commit or
roll back together), then reads the result back from :class:`HighlightView`.
``harness/api/v2/routes/highlight.py`` does request/response shape (pydantic)
and dependency wiring only.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import uuid4

from harness.core.highlight.view import (
    CREATED,
    REMOVED,
    HighlightView,
    active_highlights,
    highlight_key,
)
from harness.core.labels import check_labels
from harness.core.ports.events_v2 import EventTransactionV2, EventV2
from harness.core.ports.json_types import JsonObject, JsonValue
from harness.core.view import dispatch


class HighlightNotFoundError(LookupError):
    """No active (non-removed) highlight with this id exists in this ws."""


def new_highlight_id() -> str:
    """A fresh ``hl_<32 hex>`` id (``contracts/schemas/common/ids.json``)."""
    return f"hl_{uuid4().hex}"


def create_highlight(
    tx: EventTransactionV2,
    *,
    event_id: str,
    user_id: str,
    ws_id: str,
    anchor: JsonObject,
    labels: Sequence[str],
    label_vocabulary: Collection[str],
    topic_ids: Collection[str],
) -> JsonObject:
    """Append ``highlight.created`` and return the stored :class:`HighlightView` doc.

    Raises :class:`~harness.core.labels.LabelError` for a label outside the
    pack's vocabulary. The anchor's shape and ``start < end`` are trusted here
    (checked by the caller's pydantic model via
    :mod:`harness.core.highlight.anchor`).
    """
    check_labels(labels, vocabulary=label_vocabulary, topic_ids=topic_ids)
    highlight_id = new_highlight_id()
    payload: dict[str, JsonValue] = {
        "highlight_id": highlight_id,
        "anchor": anchor,
        "labels": list(labels),
    }
    event = EventV2(
        id=event_id,
        type=CREATED,
        actor="learner",
        user_id=user_id,
        ws_id=ws_id,
        payload=payload,
    )
    result = tx.append(event)
    if result.created:
        dispatch(result.event, tx)
    doc = HighlightView.get(tx, highlight_key(ws_id, highlight_id))
    assert doc is not None
    return doc


def remove_highlight(
    tx: EventTransactionV2, *, event_id: str, user_id: str, ws_id: str, highlight_id: str
) -> None:
    """Append ``highlight.removed``. Raises :class:`HighlightNotFoundError` unless
    an active highlight with this id exists in this ws."""
    current = HighlightView.get(tx, highlight_key(ws_id, highlight_id))
    if current is None or current.get("removed"):
        raise HighlightNotFoundError(highlight_id)
    event = EventV2(
        id=event_id,
        type=REMOVED,
        actor="learner",
        user_id=user_id,
        ws_id=ws_id,
        payload={"highlight_id": highlight_id},
    )
    result = tx.append(event)
    if result.created:
        dispatch(result.event, tx)


def list_highlights(tx: EventTransactionV2, ws_id: str) -> list[JsonObject]:
    """Current (non-removed) highlights of ``ws_id``."""
    return active_highlights(tx, ws_id)
