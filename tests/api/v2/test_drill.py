"""`/v2` drill routes (#61): list/get items without expected, record answers."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harness.api.v2.deps import event_store_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.testing.contracts import CONTRACTS_DIR
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"
GEN_ID = "0b6f9a3e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
URL = "/v2/ws/ws_1/drills/dns-answer-nxdomain/answers"


def _schemas_with_ws_created(directory: Path) -> ContractSchemas:
    """Real contracts plus a permissive ``ws.created`` until the ws resource (#57) ships it."""
    contracts = directory / "contracts"
    shutil.copytree(CONTRACTS_DIR, contracts)
    path = contracts / "schemas/events/payloads/ws.created/1.json"
    if not path.exists():
        path.parent.mkdir(parents=True)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://morphloop.dev/contracts/schemas/events/payloads/ws.created/1.json",
            "x-envelope": 2,
            "x-actors": ["learner"],
            "type": "object",
        }
        path.write_text(json.dumps(schema), encoding="utf-8")
    return ContractSchemas(contracts)


@pytest.fixture
def client(tmp_path: Path) -> Any:
    store = InMemoryEventStoreV2(_schemas_with_ws_created(tmp_path))
    with store.transaction() as tx:
        tx.append(
            EventV2(
                id=str(uuid.uuid4()),
                type="ws.created",
                actor="learner",
                user_id="usr_local",
                session_id="ses_1",
                ws_id="ws_1",
                payload={},
            )
        )
    generated = InMemoryGeneratedDocumentStore()
    body = {
        "id": GEN_ID,
        "question": "q",
        "expected": "secret",
        "answer_mode": "text",
        "labels": ["dimension:explain"],
    }
    generated.add(GeneratedDocument(resource="drill", id=GEN_ID, body=body, provenance={}))
    app, _ = build_app()
    app.state.pack_v2 = import_pack_v2(SE_PACK)
    app.state.generated_documents = generated
    app.dependency_overrides[event_store_v2_of] = lambda: store
    with TestClient(app) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c


def test_list_filters_by_labels_and_hides_expected(client: Any) -> None:
    body = client.get("/v2/drills", params={"labels": ["dimension:explain", "origin:generated"]})
    assert body.status_code == 200
    assert [i["id"] for i in body.json()["items"]] == [GEN_ID]
    everything = client.get("/v2/drills").json()["items"]
    assert len(everything) == 6 and all("expected" not in i for i in everything)


def test_get_item(client: Any) -> None:
    item = client.get("/v2/drills/dns-answer-nxdomain").json()
    assert item["answer_mode"] == "choice" and "expected" not in item
    missing = client.get("/v2/drills/nope")
    assert missing.status_code == 404 and missing.json()["code"] == "not-found"


def test_answer_is_recorded_as_event(client: Any) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(URL, json={"actual": "`NXDOMAIN`"}, headers=key)
    assert first.status_code == 201
    event = first.json()
    assert (event["type"], event["ws_id"], event["session_id"], event["user_id"]) == (
        "drill.answered",
        "ws_1",
        "ses_1",
        "usr_local",
    )
    assert event["id"] == key["Idempotency-Key"]
    assert client.post(URL, json={"actual": "`NXDOMAIN`"}, headers=key).json() == event
    assert len(client.store.read(ws_id="ws_1")) == 2  # ws.created + one answer


def test_answer_errors(client: Any) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    assert client.post(URL, json={"actual": "`NXDOMAIN`"}, headers=key).status_code == 201
    assert client.post(URL, json={"actual": "`NOERROR`"}, headers=key).status_code == 409
    assert client.post(URL, json={"actual": "`X`"}).status_code == 400
    missing_ws = client.post(URL.replace("ws_1", "ws_2"), json={"actual": "`NXDOMAIN`"})
    assert missing_ws.status_code == 404
    assert client.post(URL, json={"actual": "`NXDOMAIN`", "user_id": "usr_x"}).status_code == 422
