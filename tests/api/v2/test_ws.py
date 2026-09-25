"""`/v2` ws routes (#57): create/list/get workspaces, create threads."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harness.api.v2.deps import event_store_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.testing.fakes_v2 import InMemoryEventStoreV2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402


@pytest.fixture
def client() -> Any:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    app, _ = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: store
    with TestClient(app) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_create_ws(client: Any) -> None:
    key = _key()
    body = client.post("/v2/ws", json={"session_id": "ses_1", "labels": ["topic:x"]}, headers=key)
    assert body.status_code == 201
    event = body.json()
    assert event["id"] == key["Idempotency-Key"]
    assert event["type"] == "ws.created"
    assert event["session_id"] == "ses_1"
    assert set(event["payload"]["labels"]) == {"topic:x", "origin:learner"}
    # a resend of the same key returns the same stored event
    assert (
        client.post(
            "/v2/ws", json={"session_id": "ses_1", "labels": ["topic:x"]}, headers=key
        ).json()
        == event
    )
    assert len(client.store.read(user_id="usr_local")) == 1


def test_create_ws_invalid_session_id_is_a_client_error(client: Any) -> None:
    assert client.post("/v2/ws", json={"session_id": "not-a-session-id"}).status_code == 422
    assert (
        client.post("/v2/ws", json={"session_id": "ses_1", "labels": ["Bad Label!"]}).status_code
        == 422
    )
    assert len(client.store.read()) == 0


def test_list_and_get_ws(client: Any) -> None:
    a = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    client.post("/v2/ws", json={"session_id": "ses_2"}, headers=_key())

    everyone = client.get("/v2/ws").json()["items"]
    assert len(everyone) == 2

    scoped = client.get("/v2/ws", params={"session_id": "ses_1"}).json()["items"]
    assert [w["ws_id"] for w in scoped] == [a["ws_id"]]

    one = client.get(f"/v2/ws/{a['ws_id']}")
    assert one.status_code == 200
    assert one.json() == {
        "ws_id": a["ws_id"],
        "user_id": "usr_local",
        "session_id": "ses_1",
        "labels": ["origin:learner"],
        "created_at": a["created_at"],
    }

    missing = client.get("/v2/ws/ws_nope")
    assert missing.status_code == 404


def test_create_thread(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    key = _key()
    target = {"kind": "memo_entry", "entry_id": "ent_1"}
    resp = client.post(
        f"/v2/ws/{ws['ws_id']}/threads",
        json={"target": target, "labels": ["mode:hint"]},
        headers=key,
    )
    assert resp.status_code == 201
    event = resp.json()
    assert event["type"] == "thread.created"
    assert event["ws_id"] == ws["ws_id"] and event["session_id"] == "ses_1"
    assert event["payload"]["target"] == target
    assert event["payload"]["labels"] == ["mode:hint"]
    # a resend of the same key returns the same stored event
    assert (
        client.post(
            f"/v2/ws/{ws['ws_id']}/threads",
            json={"target": target, "labels": ["mode:hint"]},
            headers=key,
        ).json()
        == event
    )


def test_create_thread_with_no_body_is_the_main_thread(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    resp = client.post(f"/v2/ws/{ws['ws_id']}/threads")
    assert resp.status_code == 201
    assert "target" not in resp.json()["payload"]


def test_create_thread_on_missing_ws_is_404(client: Any) -> None:
    resp = client.post("/v2/ws/ws_nope/threads")
    assert resp.status_code == 404


def test_create_thread_invalid_target_kind_is_a_client_error(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    resp = client.post(f"/v2/ws/{ws['ws_id']}/threads", json={"target": {"kind": "not-a-kind"}})
    assert resp.status_code == 422
    assert len(client.store.read(ws_id=ws["ws_id"])) == 1  # only ws.created
