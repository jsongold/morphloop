"""FastAPI dependencies for `/v2`: the wired `EventStoreV2` and its per-request
transaction (ADR-0018, ADR-0008, issue #34/#52).

Real app: the store is Postgres, built once and cached on `app.state` the
first time a v2 route asks for it. No eager wiring at startup -- a route
module that never uses `EventStoreV2Dep` never pays for a database
connection.

Tests: inject an `InMemoryEventStoreV2` either by setting
`app.state.event_store_v2` before the first request, or by overriding
`event_store_v2_of` in `app.dependency_overrides` (same idiom as
`harness.api.routes.backend_of` / `harness.api.app.check_db`).

A resource route file (`harness/api/v2/routes/<resource>.py`) builds its own
service factories on top of these two dependencies; nothing here is
resource-specific.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.events_v2 import EventStoreV2, EventTransactionV2


def build_event_store_v2() -> EventStoreV2:
    """The real, Postgres-backed store (`DATABASE_URL`, `contracts/`)."""
    return PostgresEventStoreV2(create_engine_from_env(), ContractSchemas.load())


def event_store_v2_of(request: Request) -> EventStoreV2:
    """The wired store; built and cached on `app.state` on first use."""
    store: EventStoreV2 | None = getattr(request.app.state, "event_store_v2", None)
    if store is None:
        store = build_event_store_v2()
        request.app.state.event_store_v2 = store
    return store


EventStoreV2Dep = Annotated[EventStoreV2, Depends(event_store_v2_of)]


def event_transaction_v2_of(store: EventStoreV2Dep) -> Iterator[EventTransactionV2]:
    """One `EventTransactionV2` per request: commits on success, rolls back
    if the handler raises (ADR-0008)."""
    with store.transaction() as tx:
        yield tx


EventTransactionV2Dep = Annotated[EventTransactionV2, Depends(event_transaction_v2_of)]
