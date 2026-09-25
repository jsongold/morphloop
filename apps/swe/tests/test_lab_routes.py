"""`/v2` lab artifact routes and terminal socket (#62), over in-memory fakes.

The app is the SDK app with the SWE extension; the client never enters the
lifespan, so the v0.1 backend is not wired and the idle reaper does not start.
The store tracks connections: a route that read, or opened a second
transaction, while its request transaction is open fails the test (#103).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from lab_fixture import SESSION_ID, SPEC, SPEC_ID, USER_ID, WS_ID, LabFixture, build

from harness.sdk import PackV2, create_app
from harness.testing.contracts import validate
from harness.testing.fakes_v2 import seed_ws
from swe.app import EXTENSION

WS_MESSAGE_SCHEMA = "schemas/websocket/envelope/message.json"


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
    return str(response.json()["artifact_id"])


def test_lifecycle_and_check(client: TestClient, lab: LabFixture) -> None:
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    body = client.get(base).json()
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
    assert client.post(f"{base}/stop").json()["status"] == "stopped"
    conflict = client.post(f"{base}/stop")
    assert conflict.status_code == 409 and conflict.json()["code"] == "state-conflict"
    assert lab.labs.labs == {}


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
