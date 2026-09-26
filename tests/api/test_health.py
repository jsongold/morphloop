"""Tests for GET /health.

These tests never depend on a real, reachable Postgres instance: either the
DB check dependency is overridden directly, or DATABASE_URL is pointed at a
closed local port so the connection fails deterministically and fast.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest
from api_harness import build_app
from fastapi.testclient import TestClient

from harness.api.app import check_db


def _closed_port_url() -> str:
    """Return a Postgres URL pointing at a port nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # The socket above is closed on exit, so this port is free/unreachable.
    return f"postgresql+psycopg://user:pass@127.0.0.1:{port}/db"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = build_app()
    with TestClient(app) as test_client:
        yield test_client
        app.dependency_overrides.clear()


def test_health_reports_db_down_when_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", _closed_port_url())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "down"}


def test_health_reports_db_ok_with_dependency_override(client: TestClient) -> None:
    client.app.dependency_overrides[check_db] = lambda: True  # type: ignore[attr-defined]

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_reports_db_down_with_dependency_override(client: TestClient) -> None:
    client.app.dependency_overrides[check_db] = lambda: False  # type: ignore[attr-defined]

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "down"}
