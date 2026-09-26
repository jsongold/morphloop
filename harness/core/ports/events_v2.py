"""Event store v2 Port: the v0.2 event log and view documents (#34, #46).

Envelope: ``contracts/schemas/events/envelope/v2/``. Compared with v0.1
(:mod:`harness.core.ports.event_store`, kept until the v0.1 removal):

- the client-generated ``id`` is also the idempotency key: a resend with the
  same ``id`` and the same content returns the stored event, the same ``id``
  with different content raises :class:`EventIdConflictError`;
- ``position`` (DB-assigned) is the only order and ``created_at`` is the DB
  write time;
- events are read by ``user_id`` / ``session_id`` / ``ws_id``.

Views (read-only resources built from events) are stored as JSON documents
addressed by ``(view, key)``. An append and the view updates it causes run in
one :class:`EventTransactionV2` and commit or roll back together (ADR-0008).

Implementations validate every appended event against the v2 append schema
before storing it, so an invalid event is never written.

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

type ActorV2 = Literal["learner", "assistant", "system"]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventV2:
    """An append request (``envelope/v2/append.json``)."""

    id: str
    type: str
    actor: ActorV2
    user_id: str
    session_id: str | None = None
    ws_id: str | None = None
    payload: JsonObject

    def to_dict(self) -> dict[str, PlainJson]:
        """Wire form; ``session_id`` / ``ws_id`` are omitted when ``None``."""
        out: dict[str, PlainJson] = {
            "id": self.id,
            "type": self.type,
            "actor": self.actor,
            "user_id": self.user_id,
        }
        if self.session_id is not None:
            out["session_id"] = self.session_id
        if self.ws_id is not None:
            out["ws_id"] = self.ws_id
        out["payload"] = to_plain_object(self.payload)
        return out

    def same_content_as(self, event: EventV2) -> bool:
        """Whether ``event`` is a resend of this one (every envelope field equal)."""
        return EventV2.to_dict(self) == EventV2.to_dict(event)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEventV2(EventV2):
    """An event as read back (``envelope/v2/stored.json``)."""

    position: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_aware("created_at", self.created_at)
        if self.position < 1:
            raise ValueError(f"position must be >= 1, got {self.position}")

    def to_dict(self) -> dict[str, PlainJson]:
        out = EventV2.to_dict(self)
        out["position"] = self.position
        out["created_at"] = format_timestamp(self.created_at)
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class AppendResultV2:
    """``created`` is ``False`` for a resend; the caller then skips view updates."""

    event: StoredEventV2
    created: bool


class EventStoreV2Error(Exception):
    """Base class for event store v2 failures."""


class EventIdConflictError(EventStoreV2Error):
    """The ``id`` is already stored for an event with different content."""

    def __init__(self, existing: StoredEventV2) -> None:
        super().__init__(f"event id {existing.id!r} already stored with different content")
        self.existing = existing


class ViewDocumentStore(Protocol):
    """JSON documents addressed by ``(view, key)``; the store keeps them verbatim."""

    def get_view(self, view: str, key: str) -> JsonObject | None:
        """Return the document at ``(view, key)``, or ``None``."""
        ...

    def list_view(
        self, view: str, *, key_prefix: str = "", after: str | None = None, limit: int | None = None
    ) -> tuple[Sequence[tuple[str, JsonObject]], str | None]:
        """One page of ``(key, document)`` pairs whose key starts with ``key_prefix``,
        sorted by key (keyset pagination, #173).

        ``after``: the previous page's returned cursor, or ``None`` for the first
        page. ``limit``: max rows on this page, or ``None`` for every remaining
        row in one page (the cursor is then always ``None``).

        Returns ``(page, next_cursor)``; ``next_cursor`` is ``None`` on the last page.
        """
        ...

    def put_view(self, view: str, key: str, document: JsonObject) -> None:
        """Insert or replace the document at ``(view, key)``."""
        ...

    def clear_view(self, view: str) -> None:
        """Delete every document of ``view`` (used by rebuild)."""
        ...


class EventTransactionV2(ViewDocumentStore, Protocol):
    """One unit of work: appends and view reads/writes commit or roll back together.

    Must not be used after the ``with`` block of :meth:`EventStoreV2.transaction` ends.
    """

    def append(self, event: EventV2) -> AppendResultV2:
        """Validate and append ``event``; the store assigns ``position`` and ``created_at``.

        Raises ``ContractValidationError`` for an invalid event and
        :class:`EventIdConflictError` when the ``id`` is stored with other
        content. Race-safe: concurrent appends of one ``id`` store one event.
        """
        ...

    def get(self, event_id: str) -> StoredEventV2 | None:
        """The stored event with ``id`` ``event_id``, or ``None`` (idempotent replay lookup)."""
        ...


class EventStoreV2(Protocol):
    """The append-only v2 event log plus view documents."""

    def transaction(self) -> AbstractContextManager[EventTransactionV2]:
        """Open a unit of work; commit on normal exit, roll back on exception."""
        ...

    def read(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        ws_id: str | None = None,
        after_position: int = 0,
        limit: int | None = None,
    ) -> Sequence[StoredEventV2]:
        """Committed events matching every given filter, in ``position`` order.

        Only ``position > after_position``, at most ``limit``. No filter reads
        the whole log (rebuild); page with ``after_position`` = last position.
        """
        ...
