"""`/v2` drill routes (#61): list/get items without expected, record answers."""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.v2.deps import event_store_v2_of, user_id_of
from harness.api.v2.routes.drill import drill_judge_config_of, drill_service_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.drill import DrillItem, DrillService
from harness.core.drill.judge import judgment_id_of
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.testing.fakes_v2 import ConnectionTrackingStore, InMemoryEventStoreV2, seed_ws
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)
GEN_ID = "0b6f9a3e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
URL = "/v2/ws/ws_1/drills/dns-record-choice/answers"
LLM = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="judge",
    prompt_version="1",
    generation_parameters={},
)


class FakeLLM:
    def __init__(self, store: ConnectionTrackingStore, outputs: list[dict[str, Any]]) -> None:
        self.store = store
        self.outputs = outputs
        self.calls = 0
        self.requests: list[LLMRequest] = []

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        assert not self.store.tx_open
        self.calls += 1
        self.requests.append(request)
        return LLMResponse(output=self.outputs.pop(0), provenance=LLM)


@pytest.fixture
def client() -> Any:
    # The tracker fails the test if the route opens a second connection
    # (`store.read`) while the request transaction is open (#89 review).
    store = ConnectionTrackingStore(InMemoryEventStoreV2(ContractSchemas.load()))
    seed_ws(store, "ws_1", session_id="ses_1")
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
    app.state.pack_v2 = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    app.state.generated_documents = generated
    app.dependency_overrides[event_store_v2_of] = lambda: store
    with TestClient(app) as c:
        c.store = store  # type: ignore[attr-defined]
        c.app = app  # type: ignore[attr-defined]
        yield c


def test_list_filters_by_labels_and_hides_expected(client: Any) -> None:
    body = client.get("/v2/drills", params={"labels": ["dimension:explain", "origin:generated"]})
    assert body.status_code == 200
    assert [i["id"] for i in body.json()["items"]] == [GEN_ID]
    everything = client.get("/v2/drills").json()["items"]
    assert len(everything) == 4 and all("expected" not in i for i in everything)


def test_get_item(client: Any) -> None:
    item = client.get("/v2/drills/dns-record-choice").json()
    assert item["answer_mode"] == "choice" and "expected" not in item
    missing = client.get("/v2/drills/nope")
    assert missing.status_code == 404 and missing.json()["code"] == "not-found"


def test_answer_is_recorded_as_event(client: Any) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(URL, json={"actual": "A"}, headers=key)
    assert first.status_code == 201
    event = first.json()
    assert (event["type"], event["ws_id"], event["session_id"], event["user_id"]) == (
        "drill.answered",
        "ws_1",
        "ses_1",
        "usr_local",
    )
    assert event["id"] == key["Idempotency-Key"]
    assert event["judgment_status"] == "complete"
    assert client.post(URL, json={"actual": "A"}, headers=key).json() == event
    assert [e.type for e in client.store.read(ws_id="ws_1")] == [
        "ws.created",
        "drill.answered",
        "drill.judged",
    ]


def test_wrong_choice_judgment_does_not_reveal_expected(client: Any) -> None:
    response = client.post(URL, json={"actual": "AAAA"})
    assert response.status_code == 201 and response.json()["judgment_status"] == "complete"
    judged = client.store.read(ws_id="ws_1")[-1]
    assert judged.type == "drill.judged"
    assert "A" not in str(judged.payload["gap"])


def test_answer_errors(client: Any) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    assert client.post(URL, json={"actual": "A"}, headers=key).status_code == 201
    assert client.post(URL, json={"actual": "AAAA"}, headers=key).status_code == 409
    assert client.post(URL, json={"actual": "X"}).status_code == 400
    missing_ws = client.post(URL.replace("ws_1", "ws_2"), json={"actual": "A"})
    assert missing_ws.status_code == 404
    client.app.dependency_overrides[user_id_of] = lambda: "usr_other"
    try:
        other_user = client.post(URL, json={"actual": "A"})
    finally:
        del client.app.dependency_overrides[user_id_of]
    assert other_user.status_code == 404
    assert len(client.store.read(ws_id="ws_1")) == 3  # ws, answer, judgment
    assert client.post(URL, json={"actual": "A", "user_id": "usr_x"}).status_code == 422


def test_answer_values_outside_the_payload_schema_are_client_errors(client: Any) -> None:
    # Regression (PR #82 review): these used to pass the route and fail the
    # drill.answered contract inside tx.append, surfacing as 500.
    assert client.post(URL, json={"actual": ""}).status_code == 422
    assert client.post(URL, json={"actual": "x" * 20001}).status_code == 422
    assert client.post(URL, json={"artifact_id": ""}).status_code == 422
    assert client.post(URL, json={"artifact_id": "lab-1"}).status_code == 422
    assert len(client.store.read(ws_id="ws_1")) == 1  # only ws.created


def test_answer_rejects_nul_and_explicit_null(client: Any) -> None:
    # #93 hardening: NUL/surrogates are a 422 (Text), and an explicit JSON
    # `null` is rejected the same as an out-of-schema value (omission only).
    assert client.post(URL, json={"actual": "a\u0000b"}).status_code == 422
    assert client.post(URL, json={"actual": None}).status_code == 422
    assert client.post(URL, json={"artifact_id": None}).status_code == 422
    assert len(client.store.read(ws_id="ws_1")) == 1  # only ws.created


def test_answer_resend_after_pack_change_returns_stored_result(client: Any) -> None:
    # #93: a resend must replay from the stored event, not re-run item lookup
    # -- an item removed/changed in the pack must not break idempotency.
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(URL, json={"actual": "A"}, headers=key)
    assert first.status_code == 201

    def _no_items() -> DrillService:
        return DrillService([])

    client.app.dependency_overrides[drill_service_of] = _no_items
    try:
        resend = client.post(URL, json={"actual": "A"}, headers=key)
    finally:
        del client.app.dependency_overrides[drill_service_of]
    assert resend.status_code == 201, resend.text
    assert resend.json() == first.json()
    assert len(client.store.read(ws_id="ws_1")) == 3  # ws, answer, judgment


def test_text_judgment_runs_after_answer_commit_and_resend_is_singleton(client: Any) -> None:
    fake = FakeLLM(
        client.store, [{"missing": [{"description": "Review the concept.", "labels": []}]}]
    )
    client.app.state.drill_llm = fake
    client.app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
    url = URL.replace("dns-record-choice", "dns-resolver-text")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(url, json={"actual": "I do not know"}, headers=key)
    assert first.status_code == 201 and first.json()["judgment_status"] == "complete"
    assert client.post(url, json={"actual": "I do not know"}, headers=key).json() == first.json()
    assert fake.calls == 1
    assert [e.type for e in client.store.read(ws_id="ws_1")].count("drill.judged") == 1


def test_invalid_llm_output_leaves_answer_pending_then_retries(client: Any) -> None:
    fake = FakeLLM(
        client.store,
        [
            {"missing": [{"description": "", "labels": []}]},
            {"missing": []},
        ],
    )
    client.app.state.drill_llm = fake
    client.app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
    url = URL.replace("dns-record-choice", "dns-resolver-text")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(url, json={"actual": "I do not know"}, headers=key)
    assert first.status_code == 201 and first.json()["judgment_status"] == "pending"
    assert [e.type for e in client.store.read(ws_id="ws_1")] == ["ws.created", "drill.answered"]
    again = client.post(url, json={"actual": "I do not know"}, headers=key)
    assert again.json()["judgment_status"] == "complete" and fake.calls == 2
    assert [e.type for e in client.store.read(ws_id="ws_1")].count("drill.judged") == 1


def test_pending_retry_judges_the_item_as_answered_not_as_the_pack_now_has_it(client: Any) -> None:
    # #124: the pack changed the item's expected answer between the answer and
    # the retry; the retry must judge against the original material.
    fake = FakeLLM(
        client.store, [{"missing": [{"description": "", "labels": []}]}, {"missing": []}]
    )
    client.app.state.drill_llm = fake
    client.app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
    url = URL.replace("dns-record-choice", "dns-resolver-text")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    assert (
        client.post(url, json={"actual": "?"}, headers=key).json()["judgment_status"] == "pending"
    )
    original = json.loads(fake.requests[0].messages[1].content)["expected"]
    changed = DrillItem(
        id="dns-resolver-text",
        question="q",
        expected="CHANGED",
        answer_mode="text",
        labels=("origin:pack",),
    )
    client.app.dependency_overrides[drill_service_of] = lambda: DrillService([changed])
    try:
        again = client.post(url, json={"actual": "?"}, headers=key)
    finally:
        del client.app.dependency_overrides[drill_service_of]
    assert again.json()["judgment_status"] == "complete" and fake.calls == 2
    sent = json.loads(fake.requests[1].messages[1].content)
    assert sent["expected"] == original and "CHANGED" not in sent["expected"]


def test_resend_backfills_judge_inputs_for_a_legacy_answer(client: Any) -> None:
    # #124 review: an answer stored before the snapshot view existed (or by an
    # older worker) must judge on retry instead of staying pending forever.
    fake = FakeLLM(
        client.store, [{"missing": [{"description": "Review the concept.", "labels": []}]}]
    )
    client.app.state.drill_llm = fake
    client.app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
    url = URL.replace("dns-record-choice", "dns-resolver-text")
    key = str(uuid.uuid4())
    with client.store.transaction() as tx:
        tx.append(
            EventV2(
                id=key,
                type="drill.answered",
                actor="learner",
                user_id="usr_local",
                session_id="ses_1",
                ws_id="ws_1",
                payload={
                    "item_id": "dns-resolver-text",
                    "answer_mode": "text",
                    "actual": "?",
                },
            )
        )
    response = client.post(url, json={"actual": "?"}, headers={"Idempotency-Key": key})
    assert response.status_code == 201 and response.json()["judgment_status"] == "complete"
    assert fake.calls == 1
    assert [e.type for e in client.store.read(ws_id="ws_1")].count("drill.judged") == 1


def test_concurrent_retry_of_a_pending_judgment_calls_the_llm_once(client: Any) -> None:
    # #124: a second request with the same key while the first is at the LLM
    # gets `pending` and never bills a second model call.
    url = URL.replace("dns-record-choice", "dns-resolver-text")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    nested: list[Any] = []

    class RacingLLM(FakeLLM):
        def complete_structured(self, request: LLMRequest) -> LLMResponse:
            if self.calls == 0:
                worker = threading.Thread(
                    target=lambda: nested.append(
                        client.post(url, json={"actual": "?"}, headers=key)
                    )
                )
                worker.start()
                worker.join(timeout=10)
            return super().complete_structured(request)

    fake = RacingLLM(client.store, [{"missing": []}, {"missing": []}])
    client.app.state.drill_llm = fake
    client.app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
    first = client.post(url, json={"actual": "?"}, headers=key)
    assert first.json()["judgment_status"] == "complete"
    assert [r.json()["judgment_status"] for r in nested] == ["pending"]
    assert (
        client.post(url, json={"actual": "?"}, headers=key).json()["judgment_status"] == "complete"
    )
    assert fake.calls == 1
    assert [e.type for e in client.store.read(ws_id="ws_1")].count("drill.judged") == 1


def test_an_answer_squatting_the_judgment_id_is_not_a_judgment(client: Any) -> None:
    # #124: the judgment id is derived from the answer id, so a client can
    # store an ordinary answer there first. That must surface as a conflict,
    # never as `judgment_status: complete` for the later answer.
    later = str(uuid.uuid4())
    squat = client.post(
        URL, json={"actual": "A"}, headers={"Idempotency-Key": judgment_id_of(later)}
    )
    assert squat.status_code == 201
    response = client.post(URL, json={"actual": "AAAA"}, headers={"Idempotency-Key": later})
    assert response.status_code == 409 and response.json()["code"] == "idempotency-key-reused"
    assert "complete" not in response.text
    judged = [e for e in client.store.read(ws_id="ws_1") if e.type == "drill.judged"]
    assert [e.payload["answer_event_id"] for e in judged] == [judgment_id_of(later)]
