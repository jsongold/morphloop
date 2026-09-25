"""Shared `/v2` dependencies (#77): PackV2Dep, GeneratedDocumentsDep, UserIdDep, EventIdDep."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.problems import install_handlers
from harness.api.v2.deps import EventIdDep, GeneratedDocumentsDep, PackV2Dep, UserIdDep
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def _app() -> FastAPI:
    app = FastAPI()
    install_handlers(app)

    @app.post("/probe")
    def probe(
        event_id: EventIdDep, user_id: UserIdDep, docs: GeneratedDocumentsDep
    ) -> dict[str, Any]:
        return {"event_id": event_id, "user_id": user_id, "docs": type(docs).__name__}

    @app.get("/pack")
    def pack(pack: PackV2Dep) -> dict[str, Any]:
        return {"id": id(pack)}

    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    app.state.artifact_types = PACK_ARTIFACT_TYPES
    return app


def test_event_id_from_header_is_canonical_uuid() -> None:
    key = uuid.uuid4()
    client = TestClient(_app())
    body = client.post("/probe", headers={"Idempotency-Key": str(key).upper()}).json()
    assert body["event_id"] == str(key)
    assert body["docs"] == "InMemoryGeneratedDocumentStore"


def test_event_id_is_generated_when_header_absent() -> None:
    client = TestClient(_app())
    first, second = (client.post("/probe").json()["event_id"] for _ in range(2))
    assert uuid.UUID(first) and first != second


def test_non_uuid_idempotency_key_is_400_problem() -> None:
    response = TestClient(_app()).post("/probe", headers={"Idempotency-Key": "evt_1"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid-request"


def test_user_id_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_app())
    assert client.post("/probe").json()["user_id"] == "usr_local"
    monkeypatch.setenv("MORPHLOOP_USER_ID", "usr_alice")
    assert client.post("/probe").json()["user_id"] == "usr_alice"


def test_pack_v2_is_imported_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    client = TestClient(_app())
    assert client.get("/pack").json() == client.get("/pack").json()
