"""`/v2` ws routes (#57): create/list/get workspaces, create threads."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from harness.api.v2.deps import event_store_v2_of, user_id_of
from harness.core.contract_schemas import ContractSchemas
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PATHS = load_merged_openapi_v2_spec()["paths"]


def _check(body: Any, url: str, method: str, status: str) -> None:
    schema = PATHS[url][method]["responses"][status]["content"]["application/json"]["schema"]
    Draft202012Validator(schema).validate(body)


@pytest.fixture
def client() -> Any:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    app = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: store
    with TestClient(app) as c:
        c.store = store  # type: ignore[attr-defined]
        c.app = app  # type: ignore[attr-defined]
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
    b = client.post("/v2/ws", json={"session_id": "ses_2"}, headers=_key()).json()

    everyone = client.get("/v2/ws").json()["items"]
    # most recently created first (Codex finding on #90)
    assert [w["ws_id"] for w in everyone] == [b["ws_id"], a["ws_id"]]

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
        "position": a["position"],
        "main_thread_event_id": None,
    }

    missing = client.get("/v2/ws/ws_nope")
    assert missing.status_code == 404


def test_list_ws_invalid_session_id_is_a_client_error(client: Any) -> None:
    # a bad query parameter is 400 (`invalid-request`), not 422 (`harness/api/problems.py`)
    resp = client.get("/v2/ws", params={"session_id": "not-a-session-id"})
    assert resp.status_code == 400
    assert len(client.store.read()) == 0


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


def test_create_thread_null_target_is_a_client_error(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    resp = client.post(f"/v2/ws/{ws['ws_id']}/threads", json={"target": None})
    assert resp.status_code == 422
    assert len(client.store.read(ws_id=ws["ws_id"])) == 1  # only ws.created


def test_resend_of_the_main_thread_key_with_different_labels_is_409(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    key = _key()
    first = client.post(f"/v2/ws/{ws['ws_id']}/threads", json={"labels": []}, headers=key)
    assert first.status_code == 201
    second = client.post(
        f"/v2/ws/{ws['ws_id']}/threads", json={"labels": ["mode:hint"]}, headers=key
    )
    assert second.status_code == 409
    assert second.json()["code"] == "idempotency-key-reused"


def test_a_second_targetless_thread_returns_the_existing_main_thread(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    first = client.post(f"/v2/ws/{ws['ws_id']}/threads", headers=_key())
    assert first.status_code == 201
    # a different Idempotency-Key, still no target
    second = client.post(f"/v2/ws/{ws['ws_id']}/threads", headers=_key())
    assert second.status_code == 201
    assert second.json() == first.json()
    assert len(client.store.read(ws_id=ws["ws_id"])) == 2  # ws.created + one thread.created


def test_list_threads(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    main = client.post(f"/v2/ws/{ws['ws_id']}/threads", headers=_key()).json()
    target = {"kind": "textbook_block", "doc_id": "d", "block_id": "b", "highlight_id": "hl_1"}
    targeted = client.post(
        f"/v2/ws/{ws['ws_id']}/threads",
        json={"target": target, "labels": ["mode:hint"]},
        headers=_key(),
    ).json()

    listed = client.get(f"/v2/ws/{ws['ws_id']}/threads")
    assert listed.status_code == 200
    _check(listed.json(), "/ws/{ws_id}/threads", "get", "200")
    assert listed.json() == {
        "items": [
            {
                "thread_id": main["payload"]["thread_id"],
                "target": None,
                "labels": [],
                "created_at": main["created_at"],
            },
            {
                "thread_id": targeted["payload"]["thread_id"],
                "target": target,
                "labels": ["mode:hint"],
                "created_at": targeted["created_at"],
            },
        ],
        "next_cursor": None,
    }

    filtered = client.get(f"/v2/ws/{ws['ws_id']}/threads", params={"target_highlight_id": "hl_1"})
    assert filtered.json()["items"] == [listed.json()["items"][1]]

    no_match = client.get(
        f"/v2/ws/{ws['ws_id']}/threads", params={"target_highlight_id": "hl_missing"}
    )
    assert no_match.json() == {"items": [], "next_cursor": None}


def test_list_threads_on_missing_or_other_users_ws_is_404(client: Any) -> None:
    assert client.get("/v2/ws/ws_nope/threads").status_code == 404

    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    client.app.dependency_overrides[user_id_of] = lambda: "usr_other"
    try:
        other_user = client.get(f"/v2/ws/{ws['ws_id']}/threads")
    finally:
        del client.app.dependency_overrides[user_id_of]
    assert other_user.status_code == 404


def test_list_threads_invalid_target_highlight_id_is_a_client_error(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()
    resp = client.get(
        f"/v2/ws/{ws['ws_id']}/threads", params={"target_highlight_id": "not-a-highlight-id"}
    )
    assert resp.status_code == 400


def _pages(client: Any, url: str, **params: Any) -> list[list[str]]:
    """Every page of ``url`` at ``limit=2``, following ``next_cursor``."""
    pages, cursor = [], None
    while True:
        query = {**params, "limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get(f"/v2{url}", params=query).json()
        pages.append([item.get("ws_id") or item.get("thread_id") for item in body["items"]])
        cursor = body.get("next_cursor")
        if cursor is None:
            return pages


def test_list_ws_is_paged_and_scoped_to_the_caller(client: Any) -> None:
    ids = [
        client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()["ws_id"]
        for _ in range(3)
    ]
    client.app.dependency_overrides[user_id_of] = lambda: "usr_other"
    try:
        client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key())
        assert _pages(client, "/ws") == [[client.store.read(user_id="usr_other")[0].ws_id]]
    finally:
        del client.app.dependency_overrides[user_id_of]

    first = client.get("/v2/ws", params={"limit": 2}).json()
    _check(first, "/ws", "get", "200")
    assert _pages(client, "/ws") == [ids[:0:-1], ids[:1]]
    assert _pages(client, "/ws", session_id="ses_1") == [ids[:0:-1], ids[:1]]


def test_list_threads_is_paged_in_creation_order(client: Any) -> None:
    ws = client.post("/v2/ws", json={"session_id": "ses_1"}, headers=_key()).json()["ws_id"]
    ids = [
        client.post(
            f"/v2/ws/{ws}/threads", json={"target": {"kind": "artifact"}}, headers=_key()
        ).json()["payload"]["thread_id"]
        for _ in range(3)
    ]
    assert _pages(client, f"/ws/{ws}/threads") == [ids[:2], ids[2:]]
