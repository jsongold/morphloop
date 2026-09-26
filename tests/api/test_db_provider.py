"""Choosing the DB provider (#186): create_app(db=...) wins over MORPHLOOP_DB_PROVIDER."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.supabase import supabase_engine
from harness.api.app import create_app
from harness.api.v2.db import db_from_settings
from harness.cli import wiring


def test_settings_default_to_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_DB_PROVIDER", raising=False)
    assert db_from_settings() is create_engine_from_env


def test_settings_pick_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_DB_PROVIDER", "supabase")
    assert db_from_settings() is supabase_engine


def test_create_app_db_wins_over_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_DB_PROVIDER", "supabase")
    calls: list[Engine] = []

    def app_db() -> Engine:
        engine = create_engine("sqlite://")
        calls.append(engine)
        return engine

    with TestClient(create_app(db=app_db)) as client:
        assert client.get("/health").json()["db"] == "ok"
    assert len(calls) == 1


def test_migrations_prefer_the_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@pooler:6543/postgres")
    monkeypatch.delenv("MORPHLOOP_DATABASE_DIRECT_URL", raising=False)
    assert wiring.database_url().endswith(":6543/postgres")
    monkeypatch.setenv("MORPHLOOP_DATABASE_DIRECT_URL", "postgresql+psycopg://u:p@db:5432/postgres")
    assert wiring.database_url().endswith(":5432/postgres")
