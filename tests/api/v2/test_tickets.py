"""WebSocket tickets (#172): `POST /v2/auth/socket-tickets` issues a short-lived,
single-use ticket bound to the caller; `SocketUserIdDep` redeems `?ticket=` or
denies the upgrade with the 401 `unauthorized` problem (websocket README)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import jwt
import pytest
from fastapi import APIRouter, WebSocket
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012
from starlette.testclient import WebSocketDenialResponse

from harness.api.app import AppExtension, create_app
from harness.api.v2.deps import SocketUserIdDep
from harness.api.v2.tickets import AUDIENCE, socket_tickets_from_settings
from harness.core.ports.auth import AuthError
from harness.core.ports.claims import ClaimStore
from harness.testing.claims import InMemoryClaimStore
from harness.testing.openapi_v2 import V2_DIR, load_merged_openapi_v2_spec

SECRET = "test-socket-ticket-secret-0123456789abcdef"


class TwoUsers:
    def user_id(self, token: str | None) -> str:
        if token not in ("alice", "bob"):
            raise AuthError("bad token")
        return f"usr_{token}"


def make_client(auth: object | None = None, claims: ClaimStore | None = None) -> TestClient:
    router = APIRouter()

    @router.websocket("/probe")
    async def probe(websocket: WebSocket, user_id: SocketUserIdDep) -> None:
        await websocket.accept()
        await websocket.send_text(user_id)
        await websocket.close()

    app = create_app(extensions=[AppExtension(routers=(router,))], auth=auth)  # type: ignore[arg-type]
    app.state.claims = claims or InMemoryClaimStore()
    return TestClient(app)


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_SOCKET_TICKET_SECRET", SECRET)


def issue(client: TestClient, token: str) -> str:
    response = client.post("/v2/auth/socket-tickets", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    return str(response.json()["ticket"])


def connect(client: TestClient, query: str) -> str:
    with client.websocket_connect(f"/v2/probe{query}") as ws:
        return ws.receive_text()


def denied(client: TestClient, query: str) -> WebSocketDenialResponse:
    with pytest.raises(WebSocketDenialResponse) as info:
        connect(client, query)
    assert info.value.status_code == 401
    assert info.value.json()["code"] == "unauthorized"
    return info.value


def test_ticket_matches_contract_and_expires_within_ttl() -> None:
    client = make_client(TwoUsers())
    response = client.post("/v2/auth/socket-tickets", headers={"Authorization": "Bearer alice"})
    assert response.status_code == 201
    spec = load_merged_openapi_v2_spec()
    uri = (V2_DIR / "root.yaml").resolve().as_uri()
    registry = Registry().with_resource(uri, DRAFT202012.create_resource(spec))
    pointer = "#/paths/~1auth~1socket-tickets/post/responses/201/content/application~1json/schema"
    Draft202012Validator({"$ref": uri + pointer}, registry=registry).validate(response.json())
    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    assert 0 < (expires_at - datetime.now(UTC)).total_seconds() <= 60


def test_issue_needs_a_valid_bearer_token() -> None:
    response = make_client(TwoUsers()).post("/v2/auth/socket-tickets")
    assert response.status_code == 401


def test_ticket_is_bound_to_its_caller() -> None:
    client = make_client(TwoUsers())
    assert connect(client, f"?ticket={issue(client, 'alice')}") == "usr_alice"
    assert connect(client, f"?ticket={issue(client, 'bob')}") == "usr_bob"


def test_ticket_is_single_use() -> None:
    client = make_client(TwoUsers())
    ticket = issue(client, "alice")
    assert connect(client, f"?ticket={ticket}") == "usr_alice"
    assert "already used" in denied(client, f"?ticket={ticket}").json()["detail"]


def test_ticket_is_single_use_across_workers_sharing_a_claim_store() -> None:
    claims = InMemoryClaimStore()
    first, second = make_client(TwoUsers(), claims), make_client(TwoUsers(), claims)
    ticket = issue(first, "alice")
    assert connect(second, f"?ticket={ticket}") == "usr_alice"
    assert "already used" in denied(first, f"?ticket={ticket}").json()["detail"]


def test_expired_ticket_is_denied() -> None:
    client = make_client(TwoUsers())
    now = int(time.time())
    claims = {"sub": "usr_alice", "aud": AUDIENCE, "exp": now - 1, "jti": "x"}
    denied(client, f"?ticket={jwt.encode(claims, SECRET, algorithm='HS256')}")


@pytest.mark.parametrize(
    ("secret", "audience"),
    [("another-secret-0123456789abcdef-0123456789", AUDIENCE), (SECRET, "authenticated")],
)
def test_forged_user_or_foreign_token_is_denied(secret: str, audience: str) -> None:
    client = make_client(TwoUsers())
    claims = {"sub": "usr_bob", "aud": audience, "exp": int(time.time()) + 30, "jti": "y"}
    denied(client, f"?ticket={jwt.encode(claims, secret, algorithm='HS256')}")


def test_missing_ticket_is_denied_by_a_real_provider() -> None:
    denied(make_client(TwoUsers()), "")


def test_dev_provider_needs_no_ticket_or_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_SOCKET_TICKET_SECRET")
    client = make_client()
    assert connect(client, "") == "usr_local"
    ticket = client.post("/v2/auth/socket-tickets").json()["ticket"]
    assert connect(client, f"?ticket={ticket}") == "usr_local"


def test_production_requires_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_SOCKET_TICKET_SECRET")
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="MORPHLOOP_SOCKET_TICKET_SECRET"):
        socket_tickets_from_settings()
