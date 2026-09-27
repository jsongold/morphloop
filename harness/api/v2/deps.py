"""FastAPI dependencies for `/v2`: the wired `EventStoreV2` and its per-request
transaction (ADR-0018, ADR-0008, issue #34/#52).

Real app: the store is Postgres, built once and cached on `app.state` the
first time a v2 route asks for it. No eager wiring at startup -- a route
module that never uses `EventStoreV2Dep` never pays for a database
connection.

Tests: inject an `InMemoryEventStoreV2` either by setting
`app.state.event_store_v2` before the first request, or by overriding
`event_store_v2_of` in `app.dependency_overrides` (same idiom as
`harness.api.app.check_db`).

Shared per-request inputs every resource route uses (#77) -- a resource
never adds its own env var or loader:

- `PackV2Dep`: the v2 pack from `MORPHLOOP_PACK_V2_DIR`, imported once with
  the artifact types the app registered (`app.state.artifact_types`, set by
  `create_app(extensions=...)`; none for a bare app) and cached on
  `app.state.pack_v2` (tests set `app.state.pack_v2`).
- `GeneratedDocumentsDep`: runtime-generated content, Postgres by default,
  cached on `app.state.generated_documents`.
- `UserIdDep`: the caller's `usr_…` id from the bearer token, via the app's
  `AuthProvider` (`harness.api.v2.auth`; #171). A POST body never carries
  `user_id`. `SocketUserIdDep` is the same for a WebSocket route, via `?ticket=`.
- `EventIdDep`: the new event's `id`, from the `Idempotency-Key` header (a
  UUID, else 400); the server assigns a fresh UUID when it is absent.
- `replay_or_conflict`: the idempotent-replay check a POST runs first.
- `ws_or_404`: a ws-scoped resource (memo, drill, highlight) resolves its
  `ws_id` to the ws's session and owner through the `ws` view on the
  request's own transaction -- never `store.read` (a second pooled
  connection per request) and never by importing another resource's store.

A resource route file (`harness/api/v2/routes/<resource>.py`) builds its own
service factories on top of these two dependencies; nothing here is
resource-specific.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.adapters.postgres.generated_documents import PostgresGeneratedDocumentStore
from harness.api.v2.auth import auth_provider_of
from harness.api.v2.tickets import socket_tickets_of
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
from harness.core.ports.json_types import JsonObject
from harness.core.settings import Settings
from harness.core.ws import WsNotFoundError, get_ws


def harness_version() -> str:
    """The harness version recorded as provenance (ADR-0010); `HARNESS_VERSION` overrides."""
    override = Settings().harness_version
    if override:
        return override
    try:
        return version("morphloop")
    except PackageNotFoundError:  # pragma: no cover - an installed harness has metadata
        return "0.0.0"


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
        types = getattr(request.app.state, "artifact_types", ())
        pack = import_pack_v2(pack_dir, artifact_types=types)
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


class _Bearer(HTTPBearer):
    """`HTTPBearer` that also runs on WebSocket routes (no header there: `None`)."""

    async def __call__(self, request: HTTPConnection) -> HTTPAuthorizationCredentials | None:
        if not isinstance(request, Request):
            return None
        return await super().__call__(request)


_bearer = _Bearer(auto_error=False, scheme_name="bearer", bearerFormat="JWT")


def user_id_of(
    conn: HTTPConnection,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """The caller's internal id; 401 / 503 problems come from the provider's errors.

    On a WebSocket it redeems ``?ticket=`` (#172) instead: a browser cannot set
    ``Authorization`` on the upgrade. With no ticket it asks the provider with
    no token, so the dev provider's local mode needs none and a real one
    rejects the upgrade (401 ``unauthorized`` denial response).

    A sync `def` on purpose: JWKS fetches block, so FastAPI runs it in the thread pool.
    """
    if conn.scope["type"] == "websocket":
        ticket = conn.query_params.get("ticket")
        if ticket:
            return socket_tickets_of(conn).redeem(ticket)
        return auth_provider_of(conn).user_id(None)
    token = credentials.credentials if credentials else None
    return auth_provider_of(conn).user_id(token)


UserIdDep = Annotated[str, Depends(user_id_of)]
# The same dependency, named for `@router.websocket` routes: the `/v2` router
# already runs it, and FastAPI's per-connection cache redeems the ticket once.
SocketUserIdDep = UserIdDep


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


def ws_or_404(tx: EventTransactionV2, ws_id: str, *, user_id: str) -> JsonObject:
    """The ``ws`` view document of ``ws_id``, read on the request's transaction.

    404 unless the ws exists and belongs to ``user_id`` -- the same rule the
    ws routes apply, so another learner's ws is indistinguishable from none.
    """
    try:
        return get_ws(tx, ws_id, user_id=user_id)
    except WsNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
