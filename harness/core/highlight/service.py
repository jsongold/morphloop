"""Highlight create/remove/list over the v0.2 event log (#34, #60).

Framework-free orchestration: appends the event, dispatches it to every
registered ``View`` (ADR-0008: the append and every view update commit or
roll back together), then reads the result back from :class:`HighlightView`.
``harness/api/v2/routes/highlight.py`` does request/response shape (pydantic)
and dependency wiring only.

Idempotent replay: the candidate event's id is checked against the stored log
before any state lookup or vocabulary validation that could give a different
answer on a retry (issue #60 review, mirrors the common rule in
``harness/api/v2/README.md``). A same-id/same-content resend short-circuits
straight to the current view read; a same-id/different-content resend raises
:class:`~harness.core.ports.events_v2.EventIdConflictError` (409
``idempotency-key-reused``, handled globally by ``harness.api.problems``).
``harness.core`` cannot import ``harness.api.v2.deps.replay_or_conflict``
(ADR-0009), so :func:`_replay_or_conflict` below reimplements the same two
lines against the Port directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence

from harness.core.highlight.view import (
    CREATED,
    REMOVED,
    HighlightView,
    active_highlights,
    highlight_key,
)
from harness.core.labels import check_labels
from harness.core.ports.events_v2 import (
    EventIdConflictError,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.json_types import JsonObject, JsonValue
from harness.core.view import dispatch


class HighlightNotFoundError(LookupError):
    """No active (non-removed) highlight with this id exists in this ws."""


def new_highlight_id(event_id: str) -> str:
    """The ``hl_<32 hex>`` id for the highlight created by event ``event_id``.

    Derived deterministically from the event id (a UUID) so a retried POST
    with the same ``Idempotency-Key`` builds the identical payload and
    replays instead of conflicting (issue #60 review).
    """
    return f"hl_{uuid.UUID(event_id).hex}"


def _replay_or_conflict(tx: EventTransactionV2, candidate: EventV2) -> StoredEventV2 | None:
    """``None``: ``candidate.id`` is new. Otherwise ``candidate`` is a resend of
    the stored event (same content) -- raises :class:`EventIdConflictError`
    when the id is stored with other content."""
    existing = tx.get(candidate.id)
    if existing is None:
        return None
    if not candidate.same_content_as(existing):
        raise EventIdConflictError(existing)
    return existing


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

    Idempotent: a retried POST with the same event id and content replays the
    stored doc without re-checking labels. Raises
    :class:`~harness.core.labels.LabelError` for a label outside the pack's
    vocabulary on a genuinely new request. The anchor's shape and
    ``start < end`` are trusted here (checked by the caller's pydantic model
    via :mod:`harness.core.highlight.anchor`).
    """
    highlight_id = new_highlight_id(event_id)
    payload: dict[str, JsonValue] = {
        "highlight_id": highlight_id,
        "anchor": anchor,
        "labels": list(labels),
    }
    candidate = EventV2(
        id=event_id, type=CREATED, actor="learner", user_id=user_id, ws_id=ws_id, payload=payload
    )
    if _replay_or_conflict(tx, candidate) is None:
        check_labels(labels, vocabulary=label_vocabulary, topic_ids=topic_ids)
        result = tx.append(candidate)
        if result.created:
            dispatch(result.event, tx)
    doc = HighlightView.get(tx, highlight_key(ws_id, highlight_id))
    assert doc is not None
    return doc


def remove_highlight(
    tx: EventTransactionV2, *, event_id: str, user_id: str, ws_id: str, highlight_id: str
) -> None:
    """Append ``highlight.removed``. Raises :class:`HighlightNotFoundError` unless
    an active highlight with this id exists in this ws.

    Idempotent: a retried DELETE with the same event id and content replays
    (no error) even though the view has since been tombstoned by the
    original call -- the replay check runs before the active-highlight
    lookup, not after (issue #60 review).
    """
    candidate = EventV2(
        id=event_id,
        type=REMOVED,
        actor="learner",
        user_id=user_id,
        ws_id=ws_id,
        payload={"highlight_id": highlight_id},
    )
    if _replay_or_conflict(tx, candidate) is None:
        current = HighlightView.get(tx, highlight_key(ws_id, highlight_id))
        if current is None or current.get("removed"):
            raise HighlightNotFoundError(highlight_id)
        result = tx.append(candidate)
        if result.created:
            dispatch(result.event, tx)


def list_highlights(tx: EventTransactionV2, ws_id: str) -> list[JsonObject]:
    """Current (non-removed) highlights of ``ws_id``, in creation order."""
    return active_highlights(tx, ws_id)
