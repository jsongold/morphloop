"""Per-user limits (#182): `rate_limit` and `concurrency_slot` refuse with the
429 `rate-limited` problem and `Retry-After`; the SDK's LLM routes are limited."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient

from harness.api.app import AppExtension, create_app
from harness.api.v2.limits import LLM, concurrency_slot, rate_limit
from harness.core.ports.auth import AuthError
from harness.core.ports.claims import slot_key
from harness.testing.claims import InMemoryClaimStore


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


@pytest.mark.parametrize(
    "path",
    [
        "/v2/ws/ws_1/threads/thr_1/messages",
        "/v2/ws/ws_1/drills/item-1/answers",
        "/v2/notebook/generate",
    ],
)
def test_llm_routes_are_rate_limited(
    client: TestClient, claims: InMemoryClaimStore, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("MORPHLOOP_USER_LLM_CALLS_PER_MINUTE", "1")
    assert claims.consume("usr_alice", LLM, limit=1, window=timedelta(minutes=1))
    assert_rate_limited(client.post(path, json={}, headers=as_user("alice")))
