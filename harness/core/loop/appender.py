"""The single event-append path (ADR-0008, ADR-0016).

Every event the harness writes goes through :meth:`EventAppender.append`:

1. build the envelope (ids, ``occurred_at``, causation/correlation,
   ``idempotency_key``, ``attempt_id`` + ``activity_definition_id``);
2. run the redaction hook and refuse NUL code points
   (:mod:`harness.core.loop.redaction`);
3. validate the whole append request, and with it the payload, against its
   versioned contract schema (``events/envelope/append.json`` dispatches on
   ``event_type`` + ``event_version``);
4. append and update the projections in the same transaction
   (:func:`harness.core.loop.projections.apply_event`).

An idempotent resend (AC-F5) appends nothing and applies no projection update:
the update was made when the event was first stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from harness.core.contract_schemas import ContractSchemas
from harness.core.loop.ids import IdGenerator
from harness.core.loop.projections import apply_event
from harness.core.loop.redaction import RedactionHook, reject_nul
from harness.core.ports import (
    Actor,
    AppendRequest,
    AppendResult,
    EventTransaction,
    JsonObject,
)

APPEND_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/events/envelope/append.json"


@dataclass(frozen=True, slots=True, kw_only=True)
class EventDraft:
    """What a service decides about an event; the appender adds the id checks."""

    event_type: str
    event_version: int
    actor: Actor
    learner_id: str
    session_id: str
    attempt_id: str | None
    activity_definition_id: str | None
    payload: JsonObject
    occurred_at: datetime
    idempotency_key: str | None
    causation_id: str | None
    correlation_id: str | None


class EventAppender:
    """Builds, checks and appends events; the only writer of loop projections."""

    def __init__(
        self, *, schemas: ContractSchemas, ids: IdGenerator, redaction: RedactionHook
    ) -> None:
        self._schemas = schemas
        self._ids = ids
        self._redaction = redaction

    def build(self, draft: EventDraft) -> AppendRequest:
        """Redact, check and validate ``draft``; raises before anything is stored."""
        payload = self._redaction.redact(draft.event_type, draft.payload)
        request = AppendRequest(
            event_id=self._ids("evt"),
            event_type=draft.event_type,
            event_version=draft.event_version,
            occurred_at=draft.occurred_at,
            idempotency_key=draft.idempotency_key,
            causation_id=draft.causation_id,
            correlation_id=draft.correlation_id,
            learner_id=draft.learner_id,
            session_id=draft.session_id,
            attempt_id=draft.attempt_id,
            activity_definition_id=draft.activity_definition_id,
            actor=draft.actor,
            payload=payload,
        )
        wire = request.to_dict()
        reject_nul(draft.event_type, wire)
        self._schemas.validate(wire, APPEND_SCHEMA_ID)
        return request

    def append(self, tx: EventTransaction, draft: EventDraft) -> AppendResult:
        """Append ``draft`` and update its projections in the same transaction."""
        result = tx.append(self.build(draft))
        if result.created:
            apply_event(tx, result.event)
        return result
