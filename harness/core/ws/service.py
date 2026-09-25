"""ws resource: workspaces and their threads (#34, #57).

A ws is a learner's work area; a learner may hold several (no per-session
single-ws rule, ADR-0018). A thread is the topic-of-conversation unit inside
a ws and the LLM context split unit (chat, #63); it has an optional
``target`` (what it discusses, e.g. a textbook block, a memo entry, a drill
item, an artifact) and its own labels such as ``mode:hint``. With no target
it is the ws's main thread.

Both are pure identity + labels: no content lives here, and no other
resource's package is imported (ADR-0009: ids are opaque).

Appending and updating the ``ws`` view happen in the same transaction
(ADR-0008); a resent ``event_id`` (Idempotency-Key) is made to create the
identical ws/thread instead of a new one by deriving the new id from it, the
same trick chat (#63) uses for a reply's event id.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from harness.core.ports.events_v2 import ActorV2, EventTransactionV2, EventV2, StoredEventV2
from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_object
from harness.core.view import dispatch
from harness.core.ws.view import THREAD_CREATED, WS_CREATED, WsView


class WsError(Exception):
    """Base class; ``status`` is the HTTP status the API maps it to."""

    status = 400


class WsNotFoundError(WsError):
    status = 404


def _derived_id(prefix: str, event_type: str, event_id: str) -> str:
    """A stable id from the creating event's id, so a resend of the same
    Idempotency-Key creates the identical ws/thread (its content then
    matches and the resend returns the stored event) instead of a new one."""
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, f'{event_type}:{event_id}').hex}"


def _origin_label(actor: ActorV2) -> str:
    return "origin:learner" if actor == "learner" else "origin:app"


def create_ws(
    tx: EventTransactionV2,
    *,
    event_id: str,
    user_id: str,
    session_id: str,
    labels: Sequence[str] = (),
    actor: ActorV2 = "learner",
) -> StoredEventV2:
    """Append ``ws.created`` (idempotent on ``event_id``) and update the ``ws`` view.

    ``actor`` is never taken from an HTTP request body (same rule as
    ``user_id``, ``harness/api/v2/deps.py``); the API route always calls this
    with the default ``"learner"`` and only an internal caller acting for the
    app (e.g. auto-creating a session's first ws) passes another actor.
    """
    ws_id = _derived_id("ws", WS_CREATED, event_id)
    own_labels = [label for label in labels if not label.startswith("origin:")]
    payload: dict[str, PlainJson] = {"labels": [*own_labels, _origin_label(actor)]}
    result = tx.append(
        EventV2(
            id=event_id,
            type=WS_CREATED,
            actor=actor,
            user_id=user_id,
            session_id=session_id,
            ws_id=ws_id,
            payload=payload,
        )
    )
    if result.created:
        dispatch(result.event, tx)
    return result.event


def get_ws(tx: EventTransactionV2, ws_id: str, *, user_id: str) -> JsonObject:
    """The ``ws`` view document for ``ws_id``; raises :class:`WsNotFoundError`."""
    doc = WsView.get(tx, ws_id)
    if doc is None or doc["user_id"] != user_id:
        raise WsNotFoundError(f"no ws {ws_id!r}")
    return doc


def list_ws(
    tx: EventTransactionV2, *, user_id: str, session_id: str | None = None
) -> list[JsonObject]:
    """The learner's workspaces, optionally restricted to one session."""
    return [
        doc
        for _, doc in WsView.list(tx)
        if doc["user_id"] == user_id and (session_id is None or doc["session_id"] == session_id)
    ]


def create_thread(
    tx: EventTransactionV2,
    *,
    event_id: str,
    user_id: str,
    ws_id: str,
    target: JsonObject | None = None,
    labels: Sequence[str] = (),
    actor: ActorV2 = "learner",
) -> StoredEventV2:
    """Append ``thread.created`` in ``ws_id`` (idempotent on ``event_id``).

    Raises :class:`WsNotFoundError` (404) when the ws does not exist (or
    belongs to another learner).
    """
    ws = get_ws(tx, ws_id, user_id=user_id)
    thread_id = _derived_id("thr", THREAD_CREATED, event_id)
    payload: dict[str, PlainJson] = {"thread_id": thread_id, "labels": list(labels)}
    if target is not None:
        payload["target"] = to_plain_object(target)
    result = tx.append(
        EventV2(
            id=event_id,
            type=THREAD_CREATED,
            actor=actor,
            user_id=user_id,
            session_id=str(ws["session_id"]) if ws["session_id"] is not None else None,
            ws_id=ws_id,
            payload=payload,
        )
    )
    if result.created:
        dispatch(result.event, tx)
    return result.event
