"""Shared `/v2` input hardening (#92): V2Model, Text, reused Idempotency-Key -> 409."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import Field

from harness.api.problems import install_handlers
from harness.api.v2.deps import EventIdDep, EventTransactionV2Dep
from harness.api.v2.models import Text, V2Model
from harness.core.ports.events_v2 import EventV2
from harness.testing.fakes_v2 import (
    PROBE_EVENT_TYPE,
    InMemoryEventStoreV2,
    contract_schemas_with_probe,
)


class NoteIn(V2Model):
    note: Annotated[Text, Field(min_length=1, max_length=5)]


def _app(tmp_path: Path) -> tuple[FastAPI, InMemoryEventStoreV2]:
    app = FastAPI()
    install_handlers(app)
    store = InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path))
    app.state.event_store_v2 = store

    @app.post("/notes", status_code=201)
    def create(body: NoteIn, event_id: EventIdDep, tx: EventTransactionV2Dep) -> dict[str, Any]:
        event = EventV2(
            id=event_id,
            type=PROBE_EVENT_TYPE,
            actor="learner",
            user_id="usr_01",
            payload={"note": body.note},
        )
        tx.append(event)
        return {"id": event_id}

    return app, store


def _count(store: InMemoryEventStoreV2) -> int:
    return len(store.read())


def test_text_accepts_normal_text_and_field_limits_compose(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    client = TestClient(app)
    assert client.post("/notes", json={"note": "héllo"}).status_code == 201
    for bad in ("", "toolong"):
        assert client.post("/notes", json={"note": bad}).status_code == 422
    assert _count(store) == 1


def test_text_rejects_nul(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    response = TestClient(app).post("/notes", json={"note": "a\u0000b"})
    assert response.status_code == 422
    assert "NUL" in response.json()["errors"][0]["message"]
    assert _count(store) == 0


def test_v2model_rejects_unknown_fields(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    response = TestClient(app).post("/notes", json={"note": "hi", "user_id": "usr_x"})
    assert response.status_code == 422
    assert response.json()["code"] == "validation-failed"
    assert _count(store) == 0


def test_reused_idempotency_key_with_other_content_is_409(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    client = TestClient(app)
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    assert client.post("/notes", json={"note": "one"}, headers=headers).status_code == 201
    assert client.post("/notes", json={"note": "one"}, headers=headers).status_code == 201
    response = client.post("/notes", json={"note": "two"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency-key-reused"
    assert _count(store) == 1
