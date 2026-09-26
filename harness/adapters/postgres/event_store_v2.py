"""PostgreSQL implementation of the EventStoreV2 Port (#46).

Tables from migration ``c4e8a2d6f1b3``: ``events_v2`` (append-only, identity
``position``, unique ``id``, ``created_at`` defaults to ``clock_timestamp()``)
and ``view_documents_v2``.

Idempotent append is ``INSERT ... ON CONFLICT (id) DO NOTHING`` then a
``SELECT`` of the stored row. Under READ COMMITTED a concurrent insert of the
same ``id`` waits for the first transaction, so exactly one event is stored.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, cast

from sqlakeyset import select_page
from sqlalchemy import Select, column, select, table, text
from sqlalchemy.engine import Connection, Engine, RowMapping

from harness.core.contract_schemas import EVENT_V2_APPEND_ID, ContractSchemas
from harness.core.ports.events_v2 import (
    ActorV2,
    AppendResultV2,
    EventIdConflictError,
    EventStoreV2,
    EventStoreV2Error,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.json_types import JsonObject, to_plain_object

_COLUMNS = "position, id, type, actor, user_id, session_id, ws_id, payload, created_at"

_INSERT = text(
    f"""
    INSERT INTO events_v2 (id, type, actor, user_id, session_id, ws_id, payload)
    VALUES (:id, :type, :actor, :user_id, :session_id, :ws_id, CAST(:payload AS JSONB))
    ON CONFLICT (id) DO NOTHING
    RETURNING {_COLUMNS}
    """
)
_SELECT_BY_ID = text(f"SELECT {_COLUMNS} FROM events_v2 WHERE id = :id")
_GET_VIEW = text("SELECT document FROM view_documents_v2 WHERE view = :view AND key = :key")
_VIEW_DOCUMENTS = table("view_documents_v2", column("view"), column("key"), column("document"))
_PUT_VIEW = text(
    """
    INSERT INTO view_documents_v2 (view, key, document)
    VALUES (:view, :key, CAST(:document AS JSONB))
    ON CONFLICT (view, key) DO UPDATE SET document = EXCLUDED.document
    """
)
_CLEAR_VIEW = text("DELETE FROM view_documents_v2 WHERE view = :view")


def _prefix_upper_bound(prefix: str) -> str:
    """Exclusive upper bound for keys starting with ``prefix``, under the
    code-point order that ``key COLLATE "C"`` also uses (module docstring
    below), so ``key >= prefix AND key < bound`` is a sargable index range
    equivalent to ``starts_with(key, prefix)`` (#173, #190 P2 review).

    ponytail: breaks if ``prefix`` ends in the max code point (0x10FFFF); no
    key in this codebase does.
    """
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _view_select(view: str, key_prefix: str) -> Select[tuple[str, JsonObject]]:
    # COLLATE "C" orders by code point, matching Python's sorted() on str,
    # and is what the (view, key COLLATE "C") index (migration d7b1e3f5a9c2) is built on.
    key_col = _VIEW_DOCUMENTS.c.key.collate("C")
    stmt = select(_VIEW_DOCUMENTS.c.key, _VIEW_DOCUMENTS.c.document).where(
        _VIEW_DOCUMENTS.c.view == view
    )
    if key_prefix:
        stmt = stmt.where(key_col >= key_prefix).where(key_col < _prefix_upper_bound(key_prefix))
    return stmt.order_by(key_col.asc())


def _to_json(document: JsonObject) -> str:
    # allow_nan=False: NaN / Infinity are not JSON and JSONB rejects them.
    return json.dumps(to_plain_object(document), allow_nan=False, ensure_ascii=False)


def _row_to_event(row: RowMapping) -> StoredEventV2:
    return StoredEventV2(
        position=row["position"],
        id=row["id"],
        type=row["type"],
        actor=cast(ActorV2, row["actor"]),
        user_id=row["user_id"],
        session_id=row["session_id"],
        ws_id=row["ws_id"],
        payload=row["payload"],
        created_at=row["created_at"],
    )


class _Transaction:
    def __init__(self, connection: Connection, schemas: ContractSchemas) -> None:
        self._connection = connection
        self._schemas = schemas
        self.closed = False

    def _conn(self) -> Connection:
        if self.closed:
            raise EventStoreV2Error("transaction already ended")
        return self._connection

    def append(self, event: EventV2) -> AppendResultV2:
        conn = self._conn()
        self._schemas.validate(event.to_dict(), EVENT_V2_APPEND_ID)
        params: dict[str, Any] = {
            "id": event.id,
            "type": event.type,
            "actor": event.actor,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "ws_id": event.ws_id,
            "payload": _to_json(event.payload),
        }
        row = conn.execute(_INSERT, params).mappings().one_or_none()
        if row is not None:
            return AppendResultV2(event=_row_to_event(row), created=True)
        existing = _row_to_event(conn.execute(_SELECT_BY_ID, {"id": event.id}).mappings().one())
        if not event.same_content_as(existing):
            raise EventIdConflictError(existing)
        return AppendResultV2(event=existing, created=False)

    def get(self, event_id: str) -> StoredEventV2 | None:
        row = self._conn().execute(_SELECT_BY_ID, {"id": event_id}).mappings().one_or_none()
        return None if row is None else _row_to_event(row)

    def get_view(self, view: str, key: str) -> JsonObject | None:
        params = {"view": view, "key": key}
        return cast(JsonObject | None, self._conn().execute(_GET_VIEW, params).scalar_one_or_none())

    def list_view(
        self, view: str, *, key_prefix: str = "", after: str | None = None, limit: int | None = None
    ) -> tuple[Sequence[tuple[str, JsonObject]], str | None]:
        stmt = _view_select(view, key_prefix)
        if limit is None:
            rows = self._conn().execute(stmt)
            return [(row.key, cast(JsonObject, row.document)) for row in rows], None
        page = select_page(
            self._conn(), stmt, per_page=limit, after=(after,) if after is not None else None
        )
        page_rows = [(row.key, cast(JsonObject, row.document)) for row in page]
        next_cursor = page_rows[-1][0] if page.paging.has_next else None
        return page_rows, next_cursor

    def put_view(self, view: str, key: str, document: JsonObject) -> None:
        params = {"view": view, "key": key, "document": _to_json(document)}
        self._conn().execute(_PUT_VIEW, params)

    def clear_view(self, view: str) -> None:
        self._conn().execute(_CLEAR_VIEW, {"view": view})


class PostgresEventStoreV2:
    """Transactions must run at READ COMMITTED (the Postgres default); see the module doc."""

    def __init__(self, engine: Engine, schemas: ContractSchemas) -> None:
        self._engine = engine
        self._schemas = schemas

    def transaction(self) -> AbstractContextManager[EventTransactionV2]:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[_Transaction]:
        # engine.begin() commits on normal exit and rolls back on exception.
        with self._engine.begin() as connection:
            tx = _Transaction(connection, self._schemas)
            try:
                yield tx
            finally:
                tx.closed = True

    def read(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        ws_id: str | None = None,
        after_position: int = 0,
        limit: int | None = None,
    ) -> Sequence[StoredEventV2]:
        params: dict[str, Any] = {"after": after_position}
        clauses = ["position > :after"]
        for col, value in (("user_id", user_id), ("session_id", session_id), ("ws_id", ws_id)):
            if value is not None:
                clauses.append(f"{col} = :{col}")
                params[col] = value
        sql = f"SELECT {_COLUMNS} FROM events_v2 WHERE {' AND '.join(clauses)} ORDER BY position"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        with self._engine.connect() as connection:
            rows = connection.execute(text(sql), params).mappings().all()
        return [_row_to_event(row) for row in rows]


if TYPE_CHECKING:

    def _conforms(engine: Engine, schemas: ContractSchemas) -> EventStoreV2:
        return PostgresEventStoreV2(engine, schemas)
