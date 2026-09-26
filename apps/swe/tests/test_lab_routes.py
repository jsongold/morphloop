"""`/v2` lab artifact routes and terminal socket (#62), over in-memory fakes.

The app is the SDK app with the SWE extension; the client never enters the
lifespan, so the v0.1 backend is not wired and the idle reaper does not start.
The store tracks connections (``SingleConnectionStore``): a route that read,
or opened a second transaction, while its request transaction is open fails.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_fixture import SESSION_ID, SPEC, SPEC_ID, USER_ID, WS_ID, LabFixture, build
from openapi_lab import assert_check_response, assert_lab_document, assert_list_response

from harness.api.v2.routes.drill import drill_judge_config_of
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.sdk import PackV2, create_app
from harness.testing.contracts import validate
from harness.testing.fakes_v2 import ConnectionTrackingStore, seed_ws
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore
from swe.app import EXTENSION

WS_MESSAGE_SCHEMA = "schemas/websocket/envelope/message.json"

DRILL_ITEM_ID = "lab-check-drill"
LLM_PROVENANCE = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="judge",
    prompt_version="1",
    generation_parameters={},
)


class FakeLLM:
    """A drill gap judge that never opens its own transaction (#124 review)."""

    def __init__(self, store: ConnectionTrackingStore, outputs: list[dict[str, Any]]) -> None:
        self.store = store
        self.outputs = outputs
        self.calls = 0

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        assert not self.store.tx_open
        self.calls += 1
        return LLMResponse(output=self.outputs.pop(0), provenance=LLM_PROVENANCE)


def _pack_with_test_spec() -> PackV2:
    """A pack holding only the fake-adapter lab spec (routes read nothing else)."""
    return PackV2(
        pack_id="test",
        pack_version="0.1.0",
        pack_hash="0" * 64,
        manifest={},
        labels=frozenset(),
        topics=(),
        documents={"artifacts": {"artifacts/lab.json": SPEC}},
        llm_roles={},
    )


def _pack_with_drill_item() -> PackV2:
    """The fake-adapter lab spec plus one ``artifact`` item that targets its own
    ``spec.checks`` (#124), so it can be judged from a check answered against them."""
    item = {
        "id": DRILL_ITEM_ID,
        "question": "does the lab pass?",
        "expected": "run fake.exit with the target params",
        "answer_mode": "artifact",
        "labels": ["troubleshooting"],
        "artifact_ref": SPEC_ID,
    }
    pack = _pack_with_test_spec()
    return PackV2(
        pack_id=pack.pack_id,
        pack_version=pack.pack_version,
        pack_hash="sha256:" + "0" * 64,
        manifest=pack.manifest,
        labels=pack.labels,
        topics=pack.topics,
        documents={**pack.documents, "drills": {"drills/lab-check-drill.json": item}},
        llm_roles=pack.llm_roles,
    )


@pytest.fixture
def lab() -> LabFixture:
    return build()


@pytest.fixture
def client(lab: LabFixture, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MORPHLOOP_USER_ID", USER_ID)
    app = create_app(extensions=[EXTENSION])
    app.state.artifact_lab = lab.service
    app.state.event_store_v2 = lab.store
    app.state.pack_v2 = _pack_with_test_spec()
    yield TestClient(app)


def _start(client: TestClient, ws_id: str = WS_ID) -> str:
    response = client.post(f"/v2/ws/{ws_id}/artifacts", json={"spec_id": SPEC_ID})
    assert response.status_code == 201, response.text
    assert_lab_document(response.json())
    return str(response.json()["artifact_id"])


def test_lifecycle_and_check(client: TestClient, lab: LabFixture) -> None:
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    body = client.get(base).json()
    assert_lab_document(body)
    assert body["status"] == "running" and body["session_id"] == SESSION_ID
    assert client.post(f"{base}/reset").status_code == 200
    key = {"Idempotency-Key": str(uuid.uuid4())}
    assert client.post(f"{base}/stop", headers=key).json()["status"] == "stopped"
    assert client.post(f"{base}/stop", headers=key).status_code == 200  # resend
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    checked = client.post(
        f"{base}/check",
        json={"check_id": "fake.exit", "params": {"argv": ["true"], "expected_exit_code": 0}},
    )
    assert checked.status_code == 200 and checked.json()["passed"] is True
    assert_check_response(checked.json())
    stopped = client.post(f"{base}/stop").json()
    assert_lab_document(stopped)
    assert stopped["status"] == "stopped"
    conflict = client.post(f"{base}/stop")
    assert conflict.status_code == 409 and conflict.json()["code"] == "state-conflict"
    assert lab.labs.labs == {}


def test_artifact_responses_never_expose_the_internal_position(client: TestClient) -> None:
    response = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID})
    assert response.status_code == 201, response.text
    artifact_id = str(response.json()["artifact_id"])
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    documents = [
        response.json(),
        client.get(base).json(),
        client.post(f"{base}/reset").json(),
        client.post(f"{base}/stop").json(),
    ]
    assert all("position" not in document for document in documents)


def test_lab_spec_hides_fixture_and_check_internals(client: TestClient) -> None:
    response = client.get(f"/v2/artifact-specs/{SPEC_ID}")
    assert response.status_code == 200
    assert response.json() == {
        "id": SPEC_ID,
        "type": "lab",
        "labels": SPEC["labels"],
        "spec": {},
    }


def test_start_is_idempotent_and_needs_no_ws_lookup_on_resend(
    client: TestClient, lab: LabFixture
) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID}, headers=key)
    again = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID}, headers=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert len(lab.labs.labs) == 1
    reused = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": "other"}, headers=key)
    assert reused.status_code == 409 and reused.json()["code"] == "idempotency-key-reused"


def test_list_is_type_neutral_ordered_and_filterable(
    client: TestClient, lab: LabFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _start(client)
    second = _start(client)

    listed = client.get(f"/v2/ws/{WS_ID}/artifacts")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert_list_response(body)
    assert [item["artifact_id"] for item in body["items"]] == [first, second]
    assert body["items"][0] == {
        "artifact_id": first,
        "type": "lab",
        "spec_id": SPEC_ID,
        "status": "running",
    }

    same_spec = client.get(f"/v2/ws/{WS_ID}/artifacts?spec_id={SPEC_ID}")
    assert same_spec.json()["items"] == body["items"]
    assert client.get(f"/v2/ws/{WS_ID}/artifacts?spec_id=nope").json()["items"] == []

    _as_other_learner(client, lab, monkeypatch)
    assert client.get(f"/v2/ws/{WS_ID}/artifacts").status_code == 404
    assert client.get("/v2/ws/ws_other/artifacts").json()["items"] == []


def test_errors_are_problems(client: TestClient, lab: LabFixture) -> None:
    missing = client.post("/v2/ws/ws_nope/artifacts", json={"spec_id": SPEC_ID})
    assert (
        missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json"
    )
    # Another learner's ws is indistinguishable from none, and nothing is started.
    seed_ws(lab.store, "ws_other", user_id="usr_other", session_id="ses_other")
    other = client.post("/v2/ws/ws_other/artifacts", json={"spec_id": SPEC_ID})
    assert other.status_code == 404 and lab.labs.labs == {}
    artifact_id = _start(client)
    assert client.get(f"/v2/ws/ws_other/artifacts/{artifact_id}").status_code == 404
    bad = client.post(
        f"/v2/ws/{WS_ID}/artifacts/{artifact_id}/check", json={"check_id": "fake.nope"}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "validation-failed"


def test_terminal_socket(client: TestClient, lab: LabFixture) -> None:
    artifact_id = _start(client)
    with client.websocket_connect(f"/v2/artifacts/{artifact_id}/terminal") as socket:
        ready = socket.receive_json()
        validate(ready, WS_MESSAGE_SCHEMA)
        assert ready["type"] == "lab.status" and ready["payload"]["status"] == "ready"
        socket.send_json(
            {
                "type": "terminal.input",
                "protocol_version": 1,
                "correlation_id": None,
                "idempotency_key": None,
                "payload": {"data": "ls\n"},
            }
        )
        output = socket.receive_json()
        validate(output, WS_MESSAGE_SCHEMA)
        assert output["type"] == "terminal.output" and output["payload"]["data"] == "ls\n"
        socket.send_json(
            {
                "type": "ping",
                "protocol_version": 1,
                "correlation_id": "c1",
                "idempotency_key": None,
                "payload": {},
            }
        )
        pong = socket.receive_json()
        assert pong["type"] == "pong" and pong["correlation_id"] == "c1"
    types = [e.type for e in lab.store.read(ws_id=WS_ID)]
    assert "artifact.input" in types and "artifact.output" in types


def test_terminal_socket_is_scoped_to_the_learner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_id = _start(client)
    monkeypatch.setenv("MORPHLOOP_USER_ID", "usr_other")
    for target in ("art_nope", artifact_id):
        with client.websocket_connect(f"/v2/artifacts/{target}/terminal") as socket:
            error = socket.receive_json()
            validate(error, WS_MESSAGE_SCHEMA)
            assert error["type"] == "error" and error["payload"]["code"] == "not-found"


def _as_other_learner(client: TestClient, lab: LabFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_ws(lab.store, "ws_other", user_id="usr_other", session_id="ses_other")
    monkeypatch.setenv("MORPHLOOP_USER_ID", "usr_other")


def _assert_conflict_without_leak(response: object, artifact_id: str) -> None:
    assert response.status_code == 409, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["code"] == "idempotency-key-reused"
    assert artifact_id not in response.text and "lab_instance_id" not in response.text  # type: ignore[attr-defined]


def test_another_learner_reusing_the_start_key_gets_409_and_nothing_of_the_artifact(
    client: TestClient, lab: LabFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    mine = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID}, headers=key)
    artifact_id = str(mine.json()["artifact_id"])
    _as_other_learner(client, lab, monkeypatch)
    # Own ws: the key is another learner's -> 409. The first learner's ws -> 404
    # (ws_or_404 runs before anything else). Neither leaks the artifact.
    theirs = client.post("/v2/ws/ws_other/artifacts", json={"spec_id": SPEC_ID}, headers=key)
    _assert_conflict_without_leak(theirs, artifact_id)
    on_mine = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID}, headers=key)
    assert on_mine.status_code == 404 and artifact_id not in on_mine.text
    assert len(lab.labs.labs) == 1


@pytest.mark.parametrize("action", ["stop", "reset", "check"])
def test_another_learner_reusing_a_lifecycle_key_gets_409(
    client: TestClient, lab: LabFixture, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    artifact_id = _start(client)
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"check_id": "fake.exit", "params": {"argv": ["true"], "expected_exit_code": 0}}
    mine = client.post(f"/v2/ws/{WS_ID}/artifacts/{artifact_id}/{action}", json=body, headers=key)
    assert mine.status_code == 200, mine.text
    _as_other_learner(client, lab, monkeypatch)
    for ws_id in ("ws_other", WS_ID):
        theirs = client.post(
            f"/v2/ws/{ws_id}/artifacts/{artifact_id}/{action}", json=body, headers=key
        )
        _assert_conflict_without_leak(theirs, artifact_id)


def test_check_without_params_defaults_to_the_spec_target(client: TestClient) -> None:
    """#149: the GUI can't send a check's params (``learner_view`` hides the spec),
    so an omitted ``params`` defaults to the lab spec's own target for that
    ``check_id`` (``spec.checks``, #124)."""
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    checked = client.post(f"{base}/check", json={"check_id": "fake.exit"})
    assert checked.status_code == 200, checked.text
    assert_check_response(checked.json())
    assert checked.json()["passed"] is True
    assert checked.json()["params"] == {"argv": ["true"], "expected_exit_code": 0}


def test_gui_check_without_params_lets_the_drill_be_judged(
    client: TestClient, lab: LabFixture
) -> None:
    """A drill answered from a GUI check (no ``params``) is judged end to end:
    the recorded ``artifact.checked`` params match the item's target exactly,
    so ``artifact_evidence`` (#124) accepts them (#149)."""
    app = cast(FastAPI, client.app)
    app.state.pack_v2 = _pack_with_drill_item()
    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    checked = client.post(f"{base}/check", json={"check_id": "fake.exit"})
    assert checked.status_code == 200, checked.text

    fake = FakeLLM(lab.store, [{"missing": []}])
    app.state.drill_llm = fake
    app.dependency_overrides[drill_judge_config_of] = lambda: (LLM_PROVENANCE, "Judge the gap.")
    try:
        answered = client.post(
            f"/v2/ws/{WS_ID}/drills/{DRILL_ITEM_ID}/answers",
            json={"artifact_id": artifact_id},
        )
    finally:
        del app.dependency_overrides[drill_judge_config_of]
    assert answered.status_code == 201, answered.text
    assert answered.json()["judgment_status"] == "complete"
    assert fake.calls == 1
    assert [e.type for e in lab.store.read(ws_id=WS_ID)].count("drill.judged") == 1
