"""Notebook search and workspace build through the v2 routes."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.api.v2.deps import event_store_v2_of, generated_documents_of, pack_v2_of, user_id_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.session.service import create_session
from harness.testing.fakes_v2 import ConnectionTrackingStore, InMemoryEventStoreV2, seed_ws
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"
BUILD = "/v2/notebook/workspace/build"
SEARCH = "/v2/notebook/search"


@pytest.fixture
def setup() -> tuple[TestClient, str, str]:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    store = ConnectionTrackingStore(InMemoryEventStoreV2(ContractSchemas.load()))
    generated = InMemoryGeneratedDocumentStore()
    generated.add(
        GeneratedDocument(
            resource="drill",
            id="generated-secret-check",
            body={
                "id": "generated-secret-check",
                "question": "Generated question",
                "expected": "secret-needle",
                "answer_mode": "text",
                "labels": ["concept"],
            },
            provenance={},
        )
    )
    with store.transaction() as tx:
        local = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        other = create_session(
            tx,
            pack=pack,
            user_id="usr_other",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
    seed_ws(store, "ws_local", user_id="usr_local", session_id=str(local["id"]))
    seed_ws(store, "ws_other", user_id="usr_other", session_id=str(other["id"]))
    app = FastAPI()
    install_handlers(app)
    app.include_router(build_v2_router())
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[pack_v2_of] = lambda: pack
    app.dependency_overrides[generated_documents_of] = lambda: generated
    app.dependency_overrides[user_id_of] = lambda: "usr_local"
    return TestClient(app), str(local["id"]), str(other["id"])


def test_search_hits_block_memo_and_drill_without_leaking_other_user(
    setup: tuple[TestClient, str, str],
) -> None:
    client, session_id, _ = setup
    assert (
        client.post(
            "/v2/ws/ws_local/memo/entries",
            json={"actor": "learner", "body": "nameserver local note"},
        ).status_code
        == 201
    )
    client.app.dependency_overrides[user_id_of] = lambda: "usr_other"
    assert (
        client.post(
            "/v2/ws/ws_other/memo/entries",
            json={"actor": "learner", "body": "nameserver other note"},
        ).status_code
        == 201
    )
    client.app.dependency_overrides[user_id_of] = lambda: "usr_local"
    response = client.get(SEARCH, params={"q": "nameserver", "session_id": session_id})
    assert response.status_code == 200
    results = response.json()["results"]
    assert {r["kind"] for r in results} >= {"textbook_block", "memo_entry"}
    assert any(r["text"] == "nameserver local note" for r in results)
    assert all(r.get("text") != "nameserver other note" for r in results)
    drill = client.get(SEARCH, params={"q": "IPv4"}).json()["results"]
    assert any(r["kind"] == "drill_item" and r["id"] == "dns-record-choice" for r in drill)
    assert "expected" not in json.dumps(drill)
    assert client.get(SEARCH, params={"q": "secret-needle"}).json()["results"] == []


def test_build_replay_conflict_and_selection(setup: tuple[TestClient, str, str]) -> None:
    client, session_id, _ = setup
    body = {"session_id": session_id, "topic": "network.dns.resolution"}
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(BUILD, json=body, headers=headers)
    assert first.status_code == 201
    assert any(doc["id"] == "dns-resolution" for doc in first.json()["documents"])
    assert client.post(BUILD, json=body, headers=headers).json() == first.json()
    conflict = client.post(
        BUILD, json={"session_id": session_id, "labels": ["concept"]}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency-key-reused"
    by_label = client.post(BUILD, json={"session_id": session_id, "labels": ["concept"]})
    assert by_label.status_code == 201
    assert any(doc["id"] == "dns-resolution" for doc in by_label.json()["documents"])
    assert all("expected" not in item for item in by_label.json()["drills"])


def test_build_rejects_other_session_and_invalid_selectors(
    setup: tuple[TestClient, str, str],
) -> None:
    client, session_id, other_id = setup
    assert (
        client.post(BUILD, json={"session_id": other_id, "labels": ["concept"]}).status_code == 404
    )
    assert client.post(BUILD, json={"session_id": session_id}).status_code == 422
    assert (
        client.post(
            BUILD, json={"session_id": session_id, "topic": "network.dns", "labels": ["concept"]}
        ).status_code
        == 422
    )
