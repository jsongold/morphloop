"""In-memory :class:`~harness.core.ports.events_v2.EventStoreV2` for tests.

Same contract as the Postgres adapter (``tests/adapters/postgres/test_event_store_v2.py``
runs one suite against both). Transactions are serialized by one lock and work
on a private copy that is swapped in on commit.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness.core.contract_schemas import EVENT_V2_APPEND_ID, ContractSchemas
from harness.core.ports.events_v2 import (
    AppendResultV2,
    EventIdConflictError,
    EventStoreV2,
    EventStoreV2Error,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.json_types import JsonObject
from harness.testing.contracts import CONTRACTS_DIR

PROBE_EVENT_TYPE = "probe.created"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _State:
    events: list[StoredEventV2] = field(default_factory=list)
    by_id: dict[str, StoredEventV2] = field(default_factory=dict)
    views: dict[str, dict[str, JsonObject]] = field(default_factory=dict)

    def copy(self) -> _State:
        return _State(
            list(self.events), dict(self.by_id), {v: dict(d) for v, d in self.views.items()}
        )


class _Transaction:
    def __init__(
        self, state: _State, schemas: ContractSchemas, now: Callable[[], datetime]
    ) -> None:
        self.state = state
        self._schemas = schemas
        self._now = now
        self.closed = False

    def _open(self) -> _State:
        if self.closed:
            raise EventStoreV2Error("transaction already ended")
        return self.state

    def append(self, event: EventV2) -> AppendResultV2:
        state = self._open()
        self._schemas.validate(event.to_dict(), EVENT_V2_APPEND_ID)
        existing = state.by_id.get(event.id)
        if existing is not None:
            if not event.same_content_as(existing):
                raise EventIdConflictError(existing)
            return AppendResultV2(event=existing, created=False)
        stored = StoredEventV2(
            id=event.id,
            type=event.type,
            actor=event.actor,
            user_id=event.user_id,
            session_id=event.session_id,
            ws_id=event.ws_id,
            payload=event.payload,
            position=len(state.events) + 1,
            created_at=self._now(),
        )
        state.events.append(stored)
        state.by_id[stored.id] = stored
        return AppendResultV2(event=stored, created=True)

    def get(self, event_id: str) -> StoredEventV2 | None:
        return self._open().by_id.get(event_id)

    def get_view(self, view: str, key: str) -> JsonObject | None:
        return self._open().views.get(view, {}).get(key)

    def list_view(self, view: str, *, key_prefix: str = "") -> Sequence[tuple[str, JsonObject]]:
        docs = self._open().views.get(view, {})
        return [(k, docs[k]) for k in sorted(docs) if k.startswith(key_prefix)]

    def put_view(self, view: str, key: str, document: JsonObject) -> None:
        self._open().views.setdefault(view, {})[key] = document

    def clear_view(self, view: str) -> None:
        self._open().views.pop(view, None)


class InMemoryEventStoreV2:
    """``schemas`` validates appends; ``now`` supplies ``created_at``."""

    def __init__(self, schemas: ContractSchemas, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._state = _State()
        self._schemas = schemas
        self._now = now
        self._lock = threading.RLock()

    def transaction(self) -> AbstractContextManager[EventTransactionV2]:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[_Transaction]:
        with self._lock:
            tx = _Transaction(self._state.copy(), self._schemas, self._now)
            try:
                yield tx
            finally:
                tx.closed = True
            self._state = tx.state  # reached only when the block did not raise

    def read(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        ws_id: str | None = None,
        after_position: int = 0,
        limit: int | None = None,
    ) -> Sequence[StoredEventV2]:
        with self._lock:
            events = self._state.events
        selected = [
            e
            for e in events
            if e.position > after_position
            and (user_id is None or e.user_id == user_id)
            and (session_id is None or e.session_id == session_id)
            and (ws_id is None or e.ws_id == ws_id)
        ]
        return selected if limit is None else selected[:limit]


def contract_schemas_with_probe(directory: Path, *, scope: Sequence[str] = ()) -> ContractSchemas:
    """Copy ``contracts/`` into ``directory`` and add the test-only v2 type ``probe.created``.

    Its payload is ``{"note": str}`` and any actor may emit it; ``scope`` is its
    ``x-scope`` (required envelope ids). Real v2 types arrive with their
    resource PRs; until then stores are tested with this one.
    """
    contracts = directory / "contracts"
    shutil.copytree(CONTRACTS_DIR, contracts)
    path = contracts / "schemas/events/payloads" / PROBE_EVENT_TYPE / "1.json"
    path.parent.mkdir(parents=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://morphloop.dev/contracts/schemas/events/payloads/{PROBE_EVENT_TYPE}/1.json",
        "x-envelope": 2,
        "x-actors": ["learner", "assistant", "system"],
        **({"x-scope": list(scope)} if scope else {}),
        "type": "object",
        "required": ["note"],
        "properties": {"note": {"type": "string"}},
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema), encoding="utf-8")
    return ContractSchemas(contracts)


if TYPE_CHECKING:

    def _conforms(schemas: ContractSchemas) -> EventStoreV2:
        return InMemoryEventStoreV2(schemas)


def seed_ws(
    store: EventStoreV2,
    ws_id: str,
    *,
    user_id: str = "usr_local",
    session_id: str = "ses_01",
) -> None:
    """Append a ``ws.created`` for ``ws_id`` and populate the ``ws`` view, so a
    test can use a fixed ws id (``create_ws`` derives its id from the event id)."""
    from harness.core.view import dispatch
    from harness.core.ws import WS_CREATED  # importing it registers WsView

    with store.transaction() as tx:
        result = tx.append(
            EventV2(
                id=str(uuid.uuid4()),
                type=WS_CREATED,
                actor="learner",
                user_id=user_id,
                session_id=session_id,
                ws_id=ws_id,
                payload={"labels": ["origin:learner"]},
            )
        )
        dispatch(result.event, tx)


class ConnectionTrackingStore:
    """Wraps an :class:`EventStoreV2` to fail if ``.read()`` runs while its
    transaction is open -- the shape of a second, concurrent connection
    checked out of a bounded pool (#89 review)."""

    def __init__(self, inner: EventStoreV2) -> None:
        self._inner = inner
        self.tx_open = False

    @contextmanager
    def transaction(self) -> Iterator[EventTransactionV2]:
        self.tx_open = True
        try:
            with self._inner.transaction() as tx:
                yield tx
        finally:
            self.tx_open = False

    def read(self, **kwargs: Any) -> Sequence[StoredEventV2]:
        assert not self.tx_open, "store.read() must not run while a transaction is open"
        return self._inner.read(**kwargs)
