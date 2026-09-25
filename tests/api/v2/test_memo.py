"""`/v2/ws/{ws_id}/memo/entries`: append-only memo entries (#59).

Round-trips the real route through `TestClient`, with the store and pack
swapped for fakes via `app.dependency_overrides` (same idiom as
`test_router_autoinclude.py` / `test_deps.py`). Includes the input-validation
regression the wave 6 common rule asks for: an out-of-schema request is a 4xx
and never reaches `tx.append` (no event stored).
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.api.v2.deps import event_store_v2_of, pack_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import import_pack_v2
from harness.core.ports.events_v2 import EventV2
from harness.testing.contracts import CONTRACTS_DIR
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def _schemas_with_ws_created(directory: Path) -> ContractSchemas:
    """Real contracts plus a permissive ``ws.created`` until the ws resource (#57) ships it."""
    contracts = directory / "contracts"
    shutil.copytree(CONTRACTS_DIR, contracts)
    path = contracts / "schemas/events/payloads/ws.created/1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://morphloop.dev/contracts/schemas/events/payloads/ws.created/1.json",
        "x-envelope": 2,
        "x-actors": ["learner"],
        "type": "object",
    }
    path.write_text(json.dumps(schema), encoding="utf-8")
    return ContractSchemas(contracts)


def _create_ws(store: InMemoryEventStoreV2, ws_id: str, session_id: str = "ses_01") -> None:
    with store.transaction() as tx:
        tx.append(
            EventV2(
                id=str(uuid.uuid4()),
                type="ws.created",
                actor="learner",
                user_id="usr_01",
                session_id=session_id,
                ws_id=ws_id,
                payload={},
            )
        )


@pytest.fixture
def store(tmp_path: Path) -> InMemoryEventStoreV2:
    store = InMemoryEventStoreV2(_schemas_with_ws_created(tmp_path))
    _create_ws(store, "ws_01")
    return store


@pytest.fixture
def client(store: InMemoryEventStoreV2) -> TestClient:
    app = FastAPI()
    install_handlers(app)
    app.include_router(build_v2_router())
    app.dependency_overrides[event_store_v2_of] = lambda: store
    pack = import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
    app.dependency_overrides[pack_v2_of] = lambda: pack
    return TestClient(app)


def _post(client: TestClient, ws_id: str, body: dict[str, Any], **kwargs: Any) -> Any:
    return client.post(f"/v2/ws/{ws_id}/memo/entries", json=body, **kwargs)


def _memo_events(store: InMemoryEventStoreV2) -> list[Any]:
    """Stored ``memo.appended`` events, excluding the fixture's ``ws.created`` seed."""
    return [e for e in store.read() if e.type == "memo.appended"]


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


def test_reused_idempotency_key_with_different_content_is_409(client: TestClient) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = _post(client, "ws_01", {"actor": "learner", "body": "one"}, headers=key)
    assert first.status_code == 201
    second = _post(client, "ws_01", {"actor": "learner", "body": "two"}, headers=key)
    assert second.status_code == 409
    assert second.json()["code"] == "idempotency-key-reused"


def test_unknown_label_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "hi", "labels": ["nope"]})
    assert response.status_code == 422
    assert response.json()["code"] == "validation-failed"
    assert _memo_events(store) == []


def test_body_over_max_length_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "x" * 4001})
    assert response.status_code == 422
    assert _memo_events(store) == []


def test_actor_outside_learner_or_assistant_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "system", "body": "hi"})
    assert response.status_code == 422
    assert _memo_events(store) == []


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
    assert _memo_events(store) == []


def test_malformed_ws_id_is_400(client: TestClient) -> None:
    response = _post(client, "not-a-ws-id", {"actor": "learner", "body": "hi"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid-request"


def test_missing_ws_is_404(client: TestClient) -> None:
    response = _post(client, "ws_missing", {"actor": "learner", "body": "hi"})
    assert response.status_code == 404
    assert response.json()["code"] == "not-found"


def test_unknown_field_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "hi", "user_id": "usr_x"})
    assert response.status_code == 422
    assert _memo_events(store) == []


def test_body_with_nul_is_422_and_nothing_is_stored(
    client: TestClient, store: InMemoryEventStoreV2
) -> None:
    response = _post(client, "ws_01", {"actor": "learner", "body": "a\u0000b"})
    assert response.status_code == 422
    assert _memo_events(store) == []


def test_list_is_scoped_to_its_own_ws(client: TestClient, store: InMemoryEventStoreV2) -> None:
    _create_ws(store, "ws_a")
    _create_ws(store, "ws_b")
    _post(client, "ws_a", {"actor": "learner", "body": "a"})
    _post(client, "ws_b", {"actor": "learner", "body": "b"})

    listed = client.get("/v2/ws/ws_a/memo/entries").json()["entries"]
    assert [e["body"] for e in listed] == ["a"]
