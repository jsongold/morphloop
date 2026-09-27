"""Per-user limits (#182): `rate_limit` and `concurrency_slot` refuse with the
429 `rate-limited` problem and `Retry-After`; the SDK's LLM routes are limited."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from api_harness import build_app
from chat.chat_fakes import THREAD, WS, FakeToolProvider, call, store_with_thread, text
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.app import AppExtension, create_app
from harness.api.v2.deps import event_store_v2_of
from harness.api.v2.limits import LLM, concurrency_slot, rate_limit
from harness.api.v2.routes.chat import chat_llm_of
from harness.api.v2.routes.drill import drill_judge_config_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.auth import AuthError
from harness.core.ports.claims import slot_key
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.testing.claims import InMemoryClaimStore
from harness.testing.fakes_v2 import InMemoryEventStoreV2, seed_ws
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).resolve().parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"
JUDGE = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="judge",
    prompt_version="1",
    generation_parameters={},
)


class JudgeLLM:
    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(output={"missing": []}, provenance=JUDGE)


class TwoUsers:
    def user_id(self, token: str | None) -> str:
        if token not in ("alice", "bob"):
            raise AuthError("bad token")
        return f"usr_{token}"


def as_user(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def make_client(claims: InMemoryClaimStore) -> TestClient:
    router = APIRouter()

    @router.get("/raise-429")
    def raise_429() -> None:
        raise HTTPException(429, "slow down", headers={"Retry-After": "7"})

    @router.get("/fixed", dependencies=[Depends(rate_limit("probe", limit=2))])
    def fixed() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/from-settings", dependencies=[Depends(rate_limit("probe-settings"))])
    def from_settings() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/slot")
    def slot(key: str = Depends(concurrency_slot("job", cap=1))) -> dict[str, str]:
        return {"key": key}

    @router.get("/slot-settings", dependencies=[Depends(concurrency_slot("job-settings"))])
    def slot_settings() -> dict[str, bool]:
        return {"ok": True}

    app = create_app(extensions=[AppExtension(routers=(router,))], auth=TwoUsers())  # type: ignore[arg-type]
    app.state.claims = claims
    return TestClient(app)


@pytest.fixture
def claims() -> InMemoryClaimStore:
    return InMemoryClaimStore()


@pytest.fixture
def client(claims: InMemoryClaimStore) -> TestClient:
    return make_client(claims)


def assert_rate_limited(response: object, *, max_retry: int = 60) -> None:
    assert response.status_code == 429, response.text  # type: ignore[attr-defined]
    assert response.json()["code"] == "rate-limited"  # type: ignore[attr-defined]
    retry = int(response.headers["Retry-After"])  # type: ignore[attr-defined]
    assert 1 <= retry <= max_retry


def test_raised_429_is_rate_limited_problem_with_retry_after(client: TestClient) -> None:
    response = client.get("/v2/raise-429", headers=as_user("alice"))
    assert response.status_code == 429
    assert response.json()["code"] == "rate-limited"
    assert response.headers["Retry-After"] == "7"
    assert response.headers["content-type"] == "application/problem+json"


def test_rate_limit_refuses_past_limit_per_user(client: TestClient) -> None:
    for _ in range(2):
        assert client.get("/v2/fixed", headers=as_user("alice")).status_code == 200
    assert_rate_limited(client.get("/v2/fixed", headers=as_user("alice")))
    # Another user has their own window.
    assert client.get("/v2/fixed", headers=as_user("bob")).status_code == 200


def test_rate_limit_reads_settings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_USER_LLM_CALLS_PER_MINUTE", "1")
    assert client.get("/v2/from-settings", headers=as_user("alice")).status_code == 200
    assert_rate_limited(client.get("/v2/from-settings", headers=as_user("alice")))


def test_concurrency_slot_caps_holders_and_releases(
    client: TestClient, claims: InMemoryClaimStore
) -> None:
    # Released after each response, so sequential calls all get the slot.
    for _ in range(2):
        response = client.get("/v2/slot", headers=as_user("alice"))
        assert response.json() == {"key": slot_key("usr_alice", "job", 0)}
    # A concurrent holder takes the only slot: refused until it lets go.
    held = claims.acquire_slot("usr_alice", "job", holder="other", cap=1, ttl=timedelta(minutes=1))
    assert held is not None
    assert_rate_limited(client.get("/v2/slot", headers=as_user("alice")))
    assert client.get("/v2/slot", headers=as_user("bob")).status_code == 200
    claims.release(held, "other")
    assert client.get("/v2/slot", headers=as_user("alice")).status_code == 200


def test_concurrency_slot_reads_settings(
    client: TestClient, claims: InMemoryClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MORPHLOOP_USER_MAX_CONCURRENT_LABS", "2")
    for _ in range(2):
        assert claims.acquire_slot(
            "usr_alice", "job-settings", holder=f"h{_}", cap=2, ttl=timedelta(minutes=1)
        )
    assert_rate_limited(client.get("/v2/slot-settings", headers=as_user("alice")))


def exhaust_llm_quota(
    claims: InMemoryClaimStore, monkeypatch: pytest.MonkeyPatch, user: str
) -> None:
    monkeypatch.setenv("MORPHLOOP_USER_LLM_CALLS_PER_MINUTE", "1")
    assert claims.consume(user, LLM, limit=1, window=timedelta(minutes=1))


def test_notebook_generate_is_rate_limited(
    client: TestClient, claims: InMemoryClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One generate is one LLM call, run after the 202: charged at the door.
    exhaust_llm_quota(claims, monkeypatch, "usr_alice")
    assert_rate_limited(client.post("/v2/notebook/generate", json={}, headers=as_user("alice")))


CHAT_URL = f"/v2/ws/{WS}/threads/{THREAD}/messages"


@pytest.fixture
def chat(tmp_path: Path) -> tuple[TestClient, FakeToolProvider]:
    llm = FakeToolProvider([])
    app = build_app()
    store = store_with_thread(tmp_path)
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[chat_llm_of] = lambda: llm
    app.state.pack_v2 = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    return TestClient(app), llm


def test_chat_charges_every_llm_round(
    chat: tuple[TestClient, FakeToolProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, llm = chat
    monkeypatch.setenv("MORPHLOOP_USER_LLM_CALLS_PER_MINUTE", "3")
    llm.script[:] = [call("thread_target"), text("hello")]  # two calls
    assert client.post(CHAT_URL, json={"text": "hi"}).status_code == 200
    llm.script[:] = [call("thread_target"), text("again")]  # the second is refused
    assert_rate_limited(client.post(CHAT_URL, json={"text": "more"}))
    assert llm.script == [text("again")]


@pytest.fixture
def drill() -> TestClient:
    app = build_app()
    store = InMemoryEventStoreV2(ContractSchemas.load())
    seed_ws(store, "ws_1", session_id="ses_1")
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[drill_judge_config_of] = lambda: (JUDGE, "Judge the gap.")
    app.state.pack_v2 = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    app.state.drill_llm = JudgeLLM()
    return TestClient(app)


def test_drill_charges_only_the_judge_call(
    drill: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = drill.app.state.claims  # type: ignore[attr-defined]
    exhaust_llm_quota(claims, monkeypatch, "usr_local")
    # A choice answer calls no LLM: never refused.
    choice = drill.post("/v2/ws/ws_1/drills/dns-record-choice/answers", json={"actual": "A"})
    assert choice.status_code == 201, choice.text
    # A text answer is recorded, but its judge call is refused; a retry of the
    # same key after the window judges it.
    key = {"Idempotency-Key": str(uuid.uuid4())}
    url = "/v2/ws/ws_1/drills/dns-resolver-text/answers"
    assert_rate_limited(drill.post(url, json={"actual": "no idea"}, headers=key))
    monkeypatch.setenv("MORPHLOOP_USER_LLM_CALLS_PER_MINUTE", "2")
    again = drill.post(url, json={"actual": "no idea"}, headers=key)
    assert again.status_code == 201 and again.json()["judgment_status"] == "complete"
