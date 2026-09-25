"""API tests for `/v2/sessions` (#34, #54).

Builds the real app the same way `test_router_autoinclude.py` does, with the
dns-pack fixture loaded as the pack and an `InMemoryEventStoreV2` (real
`session.created` contract, no probe schema needed) swapped in via
`app.state`.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.v2.deps import user_id_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import import_pack_v2
from harness.testing.fakes_v2 import InMemoryEventStoreV2

# api_harness owns the app-under-fakes builder (see tests/api/v2/test_router_autoinclude.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"
PACK_ID = "software-engineering"


def _client(*, user_id: str | None = None, store: InMemoryEventStoreV2 | Any = None) -> TestClient:
    app, _fixture = build_app()
    app.state.pack_v2 = import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
    app.state.event_store_v2 = store or InMemoryEventStoreV2(ContractSchemas.load())
    if user_id is not None:
        app.dependency_overrides[user_id_of] = lambda: user_id
    return TestClient(app)


class _ConnectionTrackingStore:
    """Wraps `InMemoryEventStoreV2` to fail if `.read()` runs while its
    transaction is open -- the shape of a second, concurrent connection
    checked out of a bounded pool (#89 review)."""

    def __init__(self, inner: InMemoryEventStoreV2) -> None:
        self._inner = inner
        self.tx_open = False

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        self.tx_open = True
        try:
            with self._inner.transaction() as tx:
                yield tx
        finally:
            self.tx_open = False

    def read(self, **kwargs: Any) -> Any:
        assert not self.tx_open, "store.read() must not run while a transaction is open"
        return self._inner.read(**kwargs)


def _create(client: TestClient, topic_id: str, *, key: str | None = None) -> Any:
    return client.post(
        "/v2/sessions",
        json={"pack_id": PACK_ID, "topic_id": topic_id},
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def test_create_session_pins_topic_subtree_and_pack_identity() -> None:
    response = _create(_client(), "network.dns.resolution")
    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("ses_")
    assert body["pack_id"] == PACK_ID
    assert body["topic_id"] == "network.dns.resolution"
    assert body["tree"] == {
        "id": "network.dns.resolution",
        "title": "Resolution",
        "docs": ["dns-resolution"],
    }


def test_resend_of_the_same_idempotency_key_and_body_returns_the_same_session() -> None:
    client = _client()
    key = str(uuid.uuid4())
    first = _create(client, "network", key=key)
    second = _create(client, "network", key=key)
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/v2/sessions").json()) == 1


def test_reused_idempotency_key_with_a_different_body_is_409() -> None:
    client = _client()
    key = str(uuid.uuid4())
    _create(client, "network", key=key)
    response = _create(client, "network.dns", key=key)
    assert response.status_code == 409


def test_unknown_topic_is_404_and_stores_no_event() -> None:
    client = _client()
    response = _create(client, "nope")
    assert response.status_code == 404
    assert response.json()["code"] == "not-found"
    assert client.get("/v2/sessions").json() == []


def test_pack_id_that_is_not_the_loaded_pack_is_404() -> None:
    client = _client()
    response = client.post(
        "/v2/sessions",
        json={"pack_id": "some-other-pack", "topic_id": "network"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"pack_id": "Software-Engineering", "topic_id": "network"},
        {"pack_id": PACK_ID, "topic_id": "net work"},
        {"pack_id": PACK_ID, "topic_id": "network", "extra": "nope"},
        {"topic_id": "network"},
    ],
    ids=["pack_id-uppercase", "topic_id-space", "unknown-field", "missing-pack_id"],
)
def test_out_of_schema_body_is_422_and_stores_no_event(body: dict[str, Any]) -> None:
    client = _client()
    response = client.post(
        "/v2/sessions", json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 422
    assert client.get("/v2/sessions").json() == []


def test_list_and_get_session() -> None:
    client = _client()
    created = _create(client, "network.dns.records").json()
    listing = client.get("/v2/sessions").json()
    assert [s["id"] for s in listing] == [created["id"]]
    detail = client.get(f"/v2/sessions/{created['id']}")
    assert detail.status_code == 200
    assert detail.json() == created


def test_get_missing_session_is_404() -> None:
    response = _client().get("/v2/sessions/ses_missing")
    assert response.status_code == 404


def test_get_session_with_idle_minutes_includes_derived_sittings() -> None:
    client = _client()
    created = _create(client, "network").json()
    detail = client.get(f"/v2/sessions/{created['id']}", params={"idle_minutes": 30}).json()
    assert len(detail["sittings"]) == 1
    assert set(detail["sittings"][0]) == {"started_at", "ended_at"}


def test_get_session_without_idle_minutes_has_no_sittings() -> None:
    client = _client()
    created = _create(client, "network").json()
    detail = client.get(f"/v2/sessions/{created['id']}").json()
    assert "sittings" not in detail


def test_idle_minutes_over_the_timedelta_limit_is_a_4xx_not_a_500() -> None:
    client = _client()
    created = _create(client, "network").json()
    response = client.get(
        f"/v2/sessions/{created['id']}", params={"idle_minutes": 2_000_000_000_000}
    )
    # Query params are malformed-request (400), not body-schema (422); see
    # harness/api/problems.py.
    assert response.status_code == 400


def test_list_and_get_are_scoped_to_the_configured_user() -> None:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    alice = _client(user_id="usr_alice", store=store)
    bob = _client(user_id="usr_bob", store=store)
    created = _create(alice, "network").json()
    assert bob.get("/v2/sessions").json() == []
    assert bob.get(f"/v2/sessions/{created['id']}").status_code == 404
    assert [s["id"] for s in alice.get("/v2/sessions").json()] == [created["id"]]


def test_get_session_with_idle_minutes_never_reads_events_inside_an_open_transaction() -> None:
    tracking_store = _ConnectionTrackingStore(InMemoryEventStoreV2(ContractSchemas.load()))
    client = _client(store=tracking_store)
    created = _create(client, "network").json()
    response = client.get(f"/v2/sessions/{created['id']}", params={"idle_minutes": 30})
    assert response.status_code == 200
