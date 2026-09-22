"""PostgreSQL implementation of the EventStore Port (ADR-0008, ADR-0015).

Tables (created by the ``a1c3e5f7b9d2`` migration):

- ``learning_events`` -- the append-only log. ``position`` is an identity
  column, so the DB assigns a strictly increasing order; ``recorded_at``
  defaults to ``clock_timestamp()``. UPDATE / DELETE / TRUNCATE are rejected
  by triggers (AC-F1).
- ``projection_documents`` -- JSONB documents addressed by ``(name, key)``.
  The adapter stores them verbatim; core owns their meaning.

Idempotent append uses ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING``.
Under READ COMMITTED a concurrent insert of the same key makes the second
insert wait for the first transaction; if it commits, the second insert does
nothing and the following ``SELECT`` sees the committed row, so concurrent
appends with one key store exactly one event (AC-F5).

``lock_learner`` takes a transaction-scoped advisory lock on a hash of the
learner id. A hash collision only serializes two learners that did not need
it; it never lets two transactions of one learner run together.

Synchronous SQLAlchemy Core over psycopg 3, as in :mod:`.engine`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError

from harness.core.ports import (
    Actor,
    AppendRequest,
    AppendResult,
    EventStore,
    EventStoreError,
    EventTransaction,
    IdempotencyConflictError,
    JsonObject,
    StoredEvent,
    to_plain_object,
)

# First key of the two-key advisory lock, so learner locks cannot collide with
# advisory locks taken for any other purpose. ASCII "MLlr".
LEARNER_LOCK_NAMESPACE = 0x4D4C6C72

_EVENT_COLUMNS = (
    "position, recorded_at, event_id, event_type, event_version, occurred_at, "
    "idempotency_key, causation_id, correlation_id, learner_id, session_id, "
    "attempt_id, activity_definition_id, actor, payload"
)

_INSERT_EVENT = text(
    f"""
    INSERT INTO learning_events (
        event_id, event_type, event_version, occurred_at, idempotency_key,
        causation_id, correlation_id, learner_id, session_id, attempt_id,
        activity_definition_id, actor, payload
    ) VALUES (
        :event_id, :event_type, :event_version, :occurred_at, :idempotency_key,
        :causation_id, :correlation_id, :learner_id, :session_id, :attempt_id,
        :activity_definition_id, :actor, CAST(:payload AS JSONB)
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING {_EVENT_COLUMNS}
    """
)

_SELECT_BY_KEY = text(f"SELECT {_EVENT_COLUMNS} FROM learning_events WHERE idempotency_key = :key")

_LOCK_LEARNER = text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:learner_id))")

_GET_PROJECTION = text(
    "SELECT document FROM projection_documents WHERE name = :name AND key = :key"
)

# COLLATE "C" orders by code point, matching Python's sorted() on str.
_LIST_PROJECTION = text(
    """
    SELECT key, document FROM projection_documents
    WHERE name = :name AND starts_with(key, :prefix)
    ORDER BY key COLLATE "C"
    """
)

_PUT_PROJECTION = text(
    """
    INSERT INTO projection_documents (name, key, document)
    VALUES (:name, :key, CAST(:document AS JSONB))
    ON CONFLICT (name, key) DO UPDATE SET document = EXCLUDED.document
    """
)

_DELETE_PROJECTION = text("DELETE FROM projection_documents WHERE name = :name AND key = :key")

_CLEAR_PROJECTION = text("DELETE FROM projection_documents WHERE name = :name")


def _to_json(document: JsonObject) -> str:
    # allow_nan=False: NaN / Infinity are not JSON and JSONB rejects them.
    return json.dumps(to_plain_object(document), allow_nan=False, ensure_ascii=False)


def _row_to_event(row: RowMapping) -> StoredEvent:
    return StoredEvent(
        position=row["position"],
        recorded_at=row["recorded_at"],
        event_id=row["event_id"],
        event_type=row["event_type"],
        event_version=row["event_version"],
        occurred_at=row["occurred_at"],
        idempotency_key=row["idempotency_key"],
        causation_id=row["causation_id"],
        correlation_id=row["correlation_id"],
        learner_id=row["learner_id"],
        session_id=row["session_id"],
        attempt_id=row["attempt_id"],
        activity_definition_id=row["activity_definition_id"],
        actor=cast(Actor, row["actor"]),
        payload=row["payload"],
    )


class _PostgresTransaction:
    """One :class:`EventTransaction` bound to an open DB transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def _conn(self) -> Connection:
        if self._closed:
            raise EventStoreError("transaction already ended")
        return self._connection

    def append(self, request: AppendRequest) -> AppendResult:
        conn = self._conn()
        params: dict[str, Any] = {
            "event_id": request.event_id,
            "event_type": request.event_type,
            "event_version": request.event_version,
            "occurred_at": request.occurred_at,
            "idempotency_key": request.idempotency_key,
            "causation_id": request.causation_id,
            "correlation_id": request.correlation_id,
            "learner_id": request.learner_id,
            "session_id": request.session_id,
            "attempt_id": request.attempt_id,
            "activity_definition_id": request.activity_definition_id,
            "actor": request.actor,
            "payload": _to_json(request.payload),
        }
        # A savepoint keeps the outer transaction usable if the insert fails
        # (e.g. duplicate event_id), as the Port raises instead of aborting.
        try:
            with conn.begin_nested():
                row = conn.execute(_INSERT_EVENT, params).mappings().one_or_none()
        except IntegrityError as exc:
            raise EventStoreError(f"cannot append event {request.event_id!r}: {exc.orig}") from exc
        if row is not None:
            return AppendResult(event=_row_to_event(row), created=True)

        # ON CONFLICT hit: the key is stored (committed, or earlier in this
        # transaction). Only reachable with a non-None key.
        key = request.idempotency_key
        assert key is not None
        existing_row = conn.execute(_SELECT_BY_KEY, {"key": key}).mappings().one()
        existing = _row_to_event(existing_row)
        if not request.same_content_as(existing):
            raise IdempotencyConflictError(key, existing)
        return AppendResult(event=existing, created=False)

    def lock_learner(self, learner_id: str) -> None:
        self._conn().execute(
            _LOCK_LEARNER, {"namespace": LEARNER_LOCK_NAMESPACE, "learner_id": learner_id}
        )

    def get_projection(self, name: str, key: str) -> JsonObject | None:
        document = (
            self._conn().execute(_GET_PROJECTION, {"name": name, "key": key}).scalar_one_or_none()
        )
        return cast(JsonObject | None, document)

    def list_projection(
        self, name: str, *, key_prefix: str = ""
    ) -> Sequence[tuple[str, JsonObject]]:
        rows = self._conn().execute(_LIST_PROJECTION, {"name": name, "prefix": key_prefix})
        return [(row.key, cast(JsonObject, row.document)) for row in rows]

    def put_projection(self, name: str, key: str, document: JsonObject) -> None:
        self._conn().execute(
            _PUT_PROJECTION, {"name": name, "key": key, "document": _to_json(document)}
        )

    def delete_projection(self, name: str, key: str) -> None:
        self._conn().execute(_DELETE_PROJECTION, {"name": name, "key": key})

    def clear_projection(self, name: str) -> None:
        self._conn().execute(_CLEAR_PROJECTION, {"name": name})


class PostgresEventStore:
    """:class:`~harness.core.ports.EventStore` backed by PostgreSQL.

    Transactions run at the engine's isolation level, which must be READ
    COMMITTED (the Postgres default) for the idempotent-append race handling
    described in the module docstring.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def transaction(self) -> AbstractContextManager[EventTransaction]:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[_PostgresTransaction]:
        # engine.begin() commits on normal exit and rolls back on exception.
        with self._engine.begin() as connection:
            tx = _PostgresTransaction(connection)
            try:
                yield tx
            finally:
                tx.close()

    def read_session(
        self,
        session_id: str,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        return self._read(session_id, after_position, until_position, limit)

    def read_all(
        self,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        return self._read(None, after_position, until_position, limit)

    def _read(
        self,
        session_id: str | None,
        after_position: int,
        until_position: int | None,
        limit: int | None,
    ) -> list[StoredEvent]:
        clauses = ["position > :after"]
        params: dict[str, Any] = {"after": after_position}
        if session_id is not None:
            clauses.append("session_id = :session_id")
            params["session_id"] = session_id
        if until_position is not None:
            clauses.append("position <= :until")
            params["until"] = until_position
        sql = f"SELECT {_EVENT_COLUMNS} FROM learning_events WHERE {' AND '.join(clauses)} "
        sql += "ORDER BY position"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        with self._engine.connect() as connection:
            rows = connection.execute(text(sql), params).mappings().all()
        return [_row_to_event(row) for row in rows]


if TYPE_CHECKING:
    # Structural conformance to the Port, checked by mypy.
    def _conforms(engine: Engine) -> EventStore:
        return PostgresEventStore(engine)
