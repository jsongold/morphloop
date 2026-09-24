"""`/v2` lab artifact routes (#62), over in-memory fakes."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from api_harness import build_app
from artifact_lab.lab_fixture import SPEC, SPEC_ID, USER_ID, WS_ID, LabFixture, build
from fastapi.testclient import TestClient

from harness.core.pack.v2 import PackV2


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
def lab(tmp_path: Path) -> LabFixture:
    return build(tmp_path)


@pytest.fixture
def client(lab: LabFixture, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MORPHLOOP_USER_ID", USER_ID)
    app, _ = build_app()
    app.state.artifact_lab = lab.service
    app.state.pack_v2 = _pack_with_test_spec()
    with TestClient(app) as test_client:
        yield test_client


def _start(client: TestClient) -> str:
    response = client.post(f"/v2/ws/{WS_ID}/artifacts", json={"spec_id": SPEC_ID})
    assert response.status_code == 201, response.text
    return str(response.json()["artifact_id"])


def test_lifecycle_and_check(client: TestClient, lab: LabFixture) -> None:
    artifact_id = _start(client)
    base = f"/v2/ws/{WS_ID}/artifacts/{artifact_id}"
    assert client.get(base).json()["status"] == "running"
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


def test_errors_are_problems(client: TestClient) -> None:
    missing = client.post("/v2/ws/ws_nope/artifacts", json={"spec_id": SPEC_ID})
    assert (
        missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json"
    )
    artifact_id = _start(client)
    bad = client.post(
        f"/v2/ws/{WS_ID}/artifacts/{artifact_id}/check", json={"check_id": "fake.nope"}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "validation-failed"
