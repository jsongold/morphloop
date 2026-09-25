"""memo entries: the resource's only mutation is `memo.appended` (#34, #59).

Append-only log: a correction is a new entry, there is no edit or delete, and
nothing here summarizes it (the learner writes an entry, or chat is told to,
per the parent design issue #34). `entry_id` is derived from the event's own
`id` (its idempotency key) rather than a second generated id, so a resend of
the same request is guaranteed to produce the same entry.

`MemoEntries` (a :class:`~harness.core.view.View`) keeps one document per
`(ws_id, entry)`, keyed so that listing by `ws_id` prefix returns entries in
append order (the DB-assigned `position` is the only order, ADR-0008).
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from harness.core.labels import check_labels
from harness.core.pack.v2.importer import PackV2
from harness.core.ports.events_v2 import (
    ActorV2,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
    ViewDocumentStore,
)
from harness.core.ports.json_types import JsonObject, JsonValue, format_timestamp
from harness.core.view import View, dispatch

MEMO_APPENDED = "memo.appended"

_POSITION_WIDTH = 20  # zero-padded so a lexicographic sort is a position sort


def entry_id_for(event_id: str) -> str:
    """The `entry_id` for the event whose envelope `id` is `event_id`.

    One event, one entry, forever: a resend of the same `id` yields the same
    `entry_id`, so there is no second id to keep in sync with the event log.
    """
    return f"ent_{uuid.UUID(event_id).hex}"


def _key(ws_id: str, position: int) -> str:
    return f"{ws_id}/{position:0{_POSITION_WIDTH}d}"


class MemoEntries(View):
    """`(ws_id, position)` -> memo entry, in append order."""

    name = "memo_entries"
    handles: ClassVar[frozenset[str]] = frozenset({MEMO_APPENDED})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None, "memo.appended always carries a ws_id"
        entry: dict[str, JsonValue] = {
            "entry_id": event.payload["entry_id"],
            "ws_id": event.ws_id,
            "actor": event.actor,
            "body": event.payload["body"],
            "labels": event.payload.get("labels", []),
            "created_at": format_timestamp(event.created_at),
            "position": event.position,
        }
        if "source" in event.payload:
            entry["source"] = event.payload["source"]
        tx.put_view(cls.name, _key(event.ws_id, event.position), entry)

    @classmethod
    def list_for_ws(cls, tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
        """Every entry of `ws_id`, in append order."""
        return [doc for _, doc in cls.list(tx, key_prefix=f"{ws_id}/")]


def build_memo_appended(
    *,
    event_id: str,
    user_id: str,
    ws_id: str,
    session_id: str | None,
    actor: ActorV2,
    body: str,
    source: JsonObject | None,
    labels: list[str],
) -> EventV2:
    """The `memo.appended` candidate event for one request (pure; no I/O).

    The route checks `replay_or_conflict` against this candidate before
    calling `append_memo_entry`, so a resend never re-validates labels.
    """
    payload: dict[str, JsonValue] = {"entry_id": entry_id_for(event_id), "body": body}
    if source is not None:
        payload["source"] = source
    if labels:
        payload["labels"] = labels
    return EventV2(
        id=event_id,
        type=MEMO_APPENDED,
        actor=actor,
        user_id=user_id,
        session_id=session_id,
        ws_id=ws_id,
        payload=payload,
    )


def append_memo_entry(tx: EventTransactionV2, *, candidate: EventV2, pack: PackV2) -> JsonObject:
    """Validate labels, append `candidate`, update the view; return the entry.

    `candidate` must be new (the caller has already resolved idempotent
    replay via `replay_or_conflict`). Raises `harness.core.labels.LabelError`
    for a label outside the pack's vocabulary (checked before the event is
    appended, so nothing is stored).
    """
    labels = candidate.payload.get("labels", [])
    assert isinstance(labels, list)
    check_labels(labels, vocabulary=pack.labels, topic_ids=pack.topic_ids)
    result = tx.append(candidate)
    assert result.created, "caller must check replay_or_conflict before appending"
    dispatch(result.event, tx)
    return entry_for(tx, result.event)


def entry_for(tx: ViewDocumentStore, stored: StoredEventV2) -> JsonObject:
    """The stored `memo.appended` entry for `stored`, from the view."""
    assert stored.ws_id is not None, "memo.appended always carries a ws_id"
    entry = MemoEntries.get(tx, _key(stored.ws_id, stored.position))
    assert entry is not None
    return entry
