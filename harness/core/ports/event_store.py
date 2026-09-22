"""Event store / DB Port (ADR-0008, ADR-0015, ADR-0016).

Core reaches the database only through this Port. It covers:

- the append-only event log, ordered by the DB-assigned ``position``;
- idempotent append keyed by ``idempotency_key`` (AC-F5);
- a unit of work (:class:`EventTransaction`) in which an event append and the
  projection updates it causes commit or roll back together;
- per-learner serialization of learner-skill updates;
- reading the whole log in ``position`` order, so projections can be dropped
  and rebuilt (AC-F6).

Projections are exposed as a generic document store (projection name + key ->
JSON object). Core owns what the documents mean; the adapter only stores them.
This keeps the Port domain-agnostic and lets the Postgres adapter be written
before core's projection types exist.

What is NOT done here: schema validation of the envelope and payload against
``contracts/schemas/events/envelope/append.json`` and the redaction hook
(ADR-0016) both run in core before :meth:`EventTransaction.append` is called.
Adapters may assume a request is contract-valid and must store it verbatim.

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from harness.core.ports.json_types import (
    JsonObject,
    PlainJson,
    format_timestamp,
    require_aware,
    to_plain_object,
)

type Actor = Literal["learner", "tutor", "system"]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventEnvelope:
    """Envelope fields shared by an append request and a stored event.

    Field names and meaning follow ``contracts/schemas/events/envelope/fields.json``.
    ``attempt_id`` and ``activity_definition_id`` are both set or both ``None``
    (ADR-0007, ADR-0016).
    """

    event_id: str
    event_type: str
    event_version: int
    occurred_at: datetime
    idempotency_key: str | None
    causation_id: str | None
    correlation_id: str | None
    learner_id: str
    session_id: str
    attempt_id: str | None
    activity_definition_id: str | None
    actor: Actor
    payload: JsonObject

    def __post_init__(self) -> None:
        require_aware("occurred_at", self.occurred_at)
        if (self.attempt_id is None) != (self.activity_definition_id is None):
            raise ValueError("attempt_id and activity_definition_id must both be set or both None")

    def _envelope_dict(self) -> dict[str, PlainJson]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "occurred_at": format_timestamp(self.occurred_at),
            "idempotency_key": self.idempotency_key,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "learner_id": self.learner_id,
            "session_id": self.session_id,
            "attempt_id": self.attempt_id,
            "activity_definition_id": self.activity_definition_id,
            "actor": self.actor,
            "payload": to_plain_object(self.payload),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class AppendRequest(EventEnvelope):
    """What a producer hands to the store (``envelope/append.json``).

    ``position`` and ``recorded_at`` are absent: the store assigns them.
    """

    def to_dict(self) -> dict[str, PlainJson]:
        """Return the wire form, valid against ``envelope/append.json``."""
        return self._envelope_dict()

    def same_content_as(self, event: EventEnvelope) -> bool:
        """Whether ``event`` is a resend of this request for idempotency purposes.

        Compared: every field except ``idempotency_key`` (equal by definition)
        and ``event_id`` / ``occurred_at``, which a retrying producer may
        regenerate.
        """
        return (
            self.event_type == event.event_type
            and self.event_version == event.event_version
            and self.learner_id == event.learner_id
            and self.session_id == event.session_id
            and self.attempt_id == event.attempt_id
            and self.activity_definition_id == event.activity_definition_id
            and self.actor == event.actor
            and self.causation_id == event.causation_id
            and self.correlation_id == event.correlation_id
            and to_plain_object(self.payload) == to_plain_object(event.payload)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent(EventEnvelope):
    """An event as read back (``envelope/stored.json``).

    ``position`` is the only event order (ADR-0008); ``recorded_at`` is the DB
    write time and, like ``occurred_at``, is never used for ordering.
    """

    position: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        EventEnvelope.__post_init__(self)
        require_aware("recorded_at", self.recorded_at)
        if self.position < 1:
            raise ValueError(f"position must be >= 1, got {self.position}")

    def to_dict(self) -> dict[str, PlainJson]:
        """Return the wire form, valid against ``envelope/stored.json``."""
        out = self._envelope_dict()
        out["position"] = self.position
        out["recorded_at"] = format_timestamp(self.recorded_at)
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class AppendResult:
    """Outcome of :meth:`EventTransaction.append`.

    ``created`` is ``False`` when the idempotency key was already stored and
    ``event`` is the existing event. The caller must then skip the projection
    update, because it was already applied when the event was first created.
    """

    event: StoredEvent
    created: bool


class EventStoreError(Exception):
    """Base class for event store failures raised by adapters."""


class IdempotencyConflictError(EventStoreError):
    """The idempotency key is stored for an event with different content.

    Raised instead of returning the existing event, so a producer bug that
    reuses a key for a different event is not silently swallowed.
    """

    def __init__(self, idempotency_key: str, existing: StoredEvent) -> None:
        super().__init__(
            f"idempotency_key {idempotency_key!r} already stored for {existing.event_id} "
            "with different content"
        )
        self.idempotency_key = idempotency_key
        self.existing = existing


class EventTransaction(Protocol):
    """One unit of work: event appends plus projection reads and writes.

    Everything done through one transaction commits atomically when the
    ``with`` block of :meth:`EventStore.transaction` exits normally, and is
    rolled back if it raises (ADR-0008). A transaction object must not be used
    after its block ends.
    """

    def append(self, request: AppendRequest) -> AppendResult:
        """Append ``request`` and return the stored event.

        The store assigns ``position`` (strictly increasing across the whole
        log) and ``recorded_at``. Idempotency (AC-F5): if ``idempotency_key`` is
        not ``None`` and already stored, no new event is written; the existing
        event is returned with ``created=False`` if
        :meth:`AppendRequest.same_content_as` holds, else
        :class:`IdempotencyConflictError` is raised. This must be race-safe:
        concurrent appends with one key yield exactly one stored event.
        A duplicate ``event_id`` with a different key raises
        :class:`EventStoreError`.
        """
        ...

    def lock_learner(self, learner_id: str) -> None:
        """Serialize learner-skill updates for ``learner_id`` (ADR-0008).

        Blocks until no other transaction holds the lock; held until this
        transaction ends. Call it before reading the learner's skill state to
        compute an update. Re-locking within the same transaction is a no-op.
        """
        ...

    def get_projection(self, name: str, key: str) -> JsonObject | None:
        """Return the projection document at ``(name, key)``, or ``None``."""
        ...

    def list_projection(
        self, name: str, *, key_prefix: str = ""
    ) -> Sequence[tuple[str, JsonObject]]:
        """Return ``(key, document)`` pairs of projection ``name``, sorted by key.

        Only keys starting with ``key_prefix`` are returned (all keys when empty).
        """
        ...

    def put_projection(self, name: str, key: str, document: JsonObject) -> None:
        """Insert or replace the projection document at ``(name, key)``."""
        ...

    def delete_projection(self, name: str, key: str) -> None:
        """Delete the document at ``(name, key)``; a missing document is a no-op."""
        ...

    def clear_projection(self, name: str) -> None:
        """Delete every document of projection ``name`` (used by rebuild)."""
        ...


class EventStore(Protocol):
    """The event log and projection store (ADR-0008).

    Reads outside a transaction see committed data only. Appends and
    projection access go through :meth:`transaction`. The log is append-only:
    the Port offers no update or delete of events.

    Rebuild (AC-F6): in one transaction, ``clear_projection`` each projection,
    then page through :meth:`read_all` and re-apply each event; learner state is
    rebuilt from the update results recorded on ``learner_skill.updated``
    events, never by re-running the LLM (ADR-0013).
    """

    def transaction(self) -> AbstractContextManager[EventTransaction]:
        """Open a unit of work; commit on normal exit, roll back on exception."""
        ...

    def read_session(
        self,
        session_id: str,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        """Return the session's events in ``position`` order.

        Only events with ``after_position < position`` (and
        ``position <= until_position`` when given) are returned, at most
        ``limit`` of them.
        """
        ...

    def read_all(
        self,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        """Return events of every session in ``position`` order, with the same
        range and ``limit`` rules as :meth:`read_session`. Page with
        ``after_position`` = last position seen."""
        ...
