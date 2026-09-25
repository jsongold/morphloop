"""`/v2/ws/{ws_id}/threads/{thread_id}/messages` over a fake LLM (#63)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from api_harness import build_app
from chat.chat_fakes import (
    THREAD,
    WS,
    FakeToolProvider,
    call,
    store_with_thread,
    text,
)
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.v2.deps import event_store_v2_of
from harness.api.v2.routes.chat import chat_llm_of
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.llm import LLMError

URL = f"/v2/ws/{WS}/threads/{THREAD}/messages"
PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


@pytest.fixture
def llm() -> FakeToolProvider:
    return FakeToolProvider([])


@pytest.fixture
def client(tmp_path: Path, llm: FakeToolProvider) -> Iterator[TestClient]:
    store = store_with_thread(tmp_path)
    app, _ = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[chat_llm_of] = lambda: llm
    app.state.pack_v2 = import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
    with TestClient(app) as test_client:
        yield test_client


def test_send_then_list(client: TestClient, llm: FakeToolProvider) -> None:
    llm.script[:] = [call("thread_target"), text("hello")]
    sent = client.post(URL, json={"text": "hi"})
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["reply"]["text"] == "hello"
    assert body["reply"]["in_reply_to"] == body["sent"]["message_id"]
    request = llm.requests[0]
    assert request.llm.prompt_id == "assistant"
    assert (
        request.messages[0].content
        == import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
        .llm_roles["assistant"]
        .prompt_text
    )
    listed = client.get(URL)
    assert listed.json() == {"messages": [body["sent"], body["reply"]]}


def test_llm_failure_is_502(client: TestClient, llm: FakeToolProvider) -> None:
    llm.script[:] = [LLMError("down")]
    response = client.post(URL, json={"text": "hi"})
    assert response.status_code == 502
    assert response.json()["code"] == "llm-failed"
    assert [m["role"] for m in client.get(URL).json()["messages"]] == ["learner"]


def test_reused_id_with_other_text_is_409(client: TestClient, llm: FakeToolProvider) -> None:
    llm.script[:] = [text("a")]
    event_id = str(uuid.uuid4())
    client.post(URL, json={"text": "hi"}, headers={"Idempotency-Key": event_id})
    response = client.post(URL, json={"text": "other"}, headers={"Idempotency-Key": event_id})
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency-key-reused"


def test_unknown_thread_is_404(client: TestClient) -> None:
    response = client.get(f"/v2/ws/{WS}/threads/thr_missing/messages")
    assert response.status_code == 404
    assert response.json()["code"] == "not-found"


def test_send_rejects_out_of_schema_body(client: TestClient, llm: FakeToolProvider) -> None:
    # #93 hardening: unknown field / NUL text is a 422 before anything runs.
    assert client.post(URL, json={"text": "hi", "user_id": "usr_x"}).status_code == 422
    assert client.post(URL, json={"text": "a\u0000b"}).status_code == 422
    assert client.post(URL, json={"text": ""}).status_code == 422
    assert llm.requests == []
    assert client.get(URL).json() == {"messages": []}
