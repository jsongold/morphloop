"""Socket ticket single use across two API workers on real Postgres (#196).

Two apps (two workers) share one ticket secret and one Postgres ``claims``
table, each through its own engine. The same ticket is redeemed on both at
once: exactly one upgrade succeeds, the other is denied with 401.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from starlette.testclient import WebSocketDenialResponse

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.api.app import AppExtension, create_app
from harness.api.v2.deps import SocketUserIdDep


@pytest.fixture
def workers(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[TestClient]]:
    monkeypatch.setenv("MORPHLOOP_SOCKET_TICKET_SECRET", "pg-ticket-secret-0123456789abcdef-0123")
    router = APIRouter()

    @router.websocket("/probe")
    async def probe(websocket: WebSocket, user_id: SocketUserIdDep) -> None:
        await websocket.accept()
        await websocket.send_text(user_id)
        await websocket.close()

    engines = [create_engine(pg_url) for _ in range(2)]
    clients = []
    for engine in engines:
        app = create_app(extensions=[AppExtension(routers=(router,))])
        app.state.claims = PostgresClaimStore(engine)
        clients.append(TestClient(app))
    try:
        yield clients
    finally:
        for engine in engines:
            engine.dispose()


def test_same_ticket_on_two_workers_upgrades_exactly_once(workers: list[TestClient]) -> None:
    ticket = workers[0].post("/v2/auth/socket-tickets").json()["ticket"]
    barrier = threading.Barrier(2)
    accepted: list[str] = []
    denied: list[int] = []

    def connect(client: TestClient) -> None:
        barrier.wait()
        try:
            with client.websocket_connect(f"/v2/probe?ticket={ticket}") as ws:
                accepted.append(ws.receive_text())
        except WebSocketDenialResponse as exc:
            denied.append(exc.status_code)

    threads = [threading.Thread(target=connect, args=(c,)) for c in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert accepted == ["usr_local"]
    assert denied == [401]
