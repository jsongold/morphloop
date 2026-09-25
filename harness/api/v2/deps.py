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

Shared per-request inputs every resource route uses (#77) -- a resource
never adds its own env var or loader:

- `PackV2Dep`: the v2 pack from `MORPHLOOP_PACK_V2_DIR`, imported once and
  cached on `app.state.pack_v2` (tests set `app.state.pack_v2`).
- `GeneratedDocumentsDep`: runtime-generated content, Postgres by default,
  cached on `app.state.generated_documents`.
- `UserIdDep`: v0.2.0 has one learner, `MORPHLOOP_USER_ID`. A POST body never
  carries `user_id`.
- `EventIdDep`: the new event's `id`, from the `Idempotency-Key` header (a
  UUID, else 400); the server assigns a fresh UUID when it is absent.
- `replay_or_conflict`: the idempotent-replay check a POST runs first.

A resource route file (`harness/api/v2/routes/<resource>.py`) builds its own
service factories on top of these two dependencies; nothing here is
resource-specific.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.adapters.postgres.generated_documents import PostgresGeneratedDocumentStore
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import PackV2, import_pack_v2
from harness.core.ports.events_v2 import (
    EventIdConflictError,
    EventStoreV2,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.generated_documents import GeneratedDocumentStore
from harness.core.settings import Settings


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


def pack_v2_of(request: Request) -> PackV2:
    """The v2 pack; imported from `MORPHLOOP_PACK_V2_DIR` and cached on first use."""
    pack: PackV2 | None = getattr(request.app.state, "pack_v2", None)
    if pack is None:
        pack_dir = Settings().morphloop_pack_v2_dir
        if pack_dir is None:
            raise RuntimeError("MORPHLOOP_PACK_V2_DIR is not set")
        pack = import_pack_v2(pack_dir)
        request.app.state.pack_v2 = pack
    return pack


PackV2Dep = Annotated[PackV2, Depends(pack_v2_of)]


def generated_documents_of(request: Request) -> GeneratedDocumentStore:
    """The wired store; Postgres, built and cached on `app.state` on first use."""
    store: GeneratedDocumentStore | None = getattr(request.app.state, "generated_documents", None)
    if store is None:
        store = PostgresGeneratedDocumentStore(create_engine_from_env())
        request.app.state.generated_documents = store
    return store


GeneratedDocumentsDep = Annotated[GeneratedDocumentStore, Depends(generated_documents_of)]


def user_id_of() -> str:
    """The single v0.2.0 learner (`MORPHLOOP_USER_ID`)."""
    return Settings().morphloop_user_id


UserIdDep = Annotated[str, Depends(user_id_of)]


def event_id_of(
    idempotency_key: Annotated[uuid.UUID | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """The new event's `id`: the `Idempotency-Key` header in canonical lowercase
    form, or a fresh UUID. A non-UUID header fails request validation (400)."""
    return str(idempotency_key or uuid.uuid4())


EventIdDep = Annotated[str, Depends(event_id_of)]


def replay_or_conflict(tx: EventTransactionV2, candidate: EventV2) -> StoredEventV2 | None:
    """Idempotent replay check, run before any other validation or append.

    ``None``: the id is new. The stored event: ``candidate`` is a resend of it
    (same user and content). Raises `EventIdConflictError` (409
    `idempotency-key-reused`) when the id is stored with other content.
    """
    existing = tx.get(candidate.id)
    if existing is None:
        return None
    if not candidate.same_content_as(existing):
        raise EventIdConflictError(existing)
    return existing
