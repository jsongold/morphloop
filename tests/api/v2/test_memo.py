"""`/v2/ws/{ws_id}/memo/entries`: append-only memo entries (#59).

Round-trips the real route through `TestClient`, with the store and pack
swapped for fakes via `app.dependency_overrides` (same idiom as
`test_router_autoinclude.py` / `test_deps.py`). Includes the input-validation
regression the wave 6 common rule asks for: an out-of-schema request is a 4xx
and never reaches `tx.append` (no event stored).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.api.v2.deps import event_store_v2_of, pack_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import import_pack_v2
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


@pytest.fixture
def store() -> InMemoryEventStoreV2:
    return InMemoryEventStoreV2(ContractSchemas.load())


@pytest.fixture
def client(store: InMemoryEventStoreV2) -> TestClient:
    app = FastAPI()
    install_handlers(app)
    app.include_router(build_v2_router())
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[pack_v2_of] = lambda: import_pack_v2(PACK_DIR)
    return TestClient(app)


def _post(client: TestClient, ws_id: str, body: dict[str, Any], **kwargs: Any) -> Any:
    return client.post(f"/v2/ws/{ws_id}/memo/entries", json=body, **kwargs)


def test_append_then_list_round_trip(client: TestClient) -> None:
    created = _post(client, "ws_01", {"actor": "learner", "body": "hi", "labels": ["concept"]})
    assert created.status_code == 201
    entry = created.json()
    assert entry["entry_id"].startswith("ent_")
    assert entry["ws_id"] == "ws_01"
    assert entry["actor"] == "learner"
    assert entry["labels"] == ["concept"]

    listed = client.get("/v2/ws/ws_01/memo/entries")
    assert listed.status_code == 200
    assert listed.json() == {"entries": [entry]}


def test_source_is_returned(client: TestClient) -> None:
    created = _post(
        client, "ws_01", {"actor": "assistant", "body": "hi", "source": {"thread_id": "thr_1"}}
    )
    assert created.status_code == 201
    assert created.json()["source"] == {"thread_id": "thr_1"}


def test_resend_with_the_same_idempotency_key_appends_once(client: TestClient) -> None:
    key = str(uuid.uuid4())
    body = {"actor": "learner", "body": "same note"}
    first = _post(client, "ws_01", body, headers={"Idempotency-Key": key})
    second = _post(client, "ws_01", body, headers={"Idempotency-Key": key})

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert client.get("/v2/ws/ws_01/memo/entries").json()["entries"] == [first.json()]


def test_unknown_label_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "hi", "labels": ["nope"]})
    assert response.status_code == 422
    assert response.json()["code"] == "validation-failed"
    assert store.read() == []


def test_body_over_max_length_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "x" * 4001})
    assert response.status_code == 422
    assert store.read() == []


def test_actor_outside_learner_or_assistant_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "system", "body": "hi"})
    assert response.status_code == 422
    assert store.read() == []


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"highlight_id": "hl_1", "thread_id": "thr_1"},
    ],
)
def test_source_must_set_exactly_one_id(
    client: TestClient, store: InMemoryEventStoreV2, source: dict[str, str]
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "hi", "source": source})
    assert response.status_code == 422
    assert store.read() == []


def test_malformed_ws_id_is_400(client: TestClient) -> None:
    response = _post(client, "not-a-ws-id", {"actor": "learner", "body": "hi"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid-request"


def test_list_is_scoped_to_its_own_ws(client: TestClient) -> None:
    _post(client, "ws_a", {"actor": "learner", "body": "a"})
    _post(client, "ws_b", {"actor": "learner", "body": "b"})

    listed = client.get("/v2/ws/ws_a/memo/entries").json()["entries"]
    assert [e["body"] for e in listed] == ["a"]
