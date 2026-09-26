"""Tests for the CORS middleware's multi-origin support (#169)."""

from __future__ import annotations

import pytest
from api_harness import build_app
from fastapi.testclient import TestClient


def test_single_web_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_ORIGINS", raising=False)
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:3000")
    client = TestClient(build_app())

    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_web_origins_allows_a_second_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ORIGINS", "https://a.example.com,https://b.example.com")
    client = TestClient(build_app())

    response = client.get("/health", headers={"Origin": "https://b.example.com"})

    assert response.headers["access-control-allow-origin"] == "https://b.example.com"


def test_web_origins_rejects_an_origin_not_in_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ORIGINS", "https://a.example.com,https://b.example.com")
    client = TestClient(build_app())

    response = client.get("/health", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers
