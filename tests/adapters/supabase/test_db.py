"""The supabase DB provider disables psycopg prepared statements (Supavisor, #186)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import event

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.supabase import supabase_engine


class _Captured(Exception):
    pass


def _connect_params(engine: Any) -> dict[str, Any]:
    """The kwargs SQLAlchemy would pass to ``psycopg.connect`` (no server needed)."""
    seen: dict[str, Any] = {}

    def capture(dialect: Any, conn_rec: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        seen.update(cparams)
        raise _Captured

    event.listen(engine, "do_connect", capture)
    with pytest.raises(_Captured):
        engine.connect()
    engine.dispose()
    return seen


def test_supabase_engine_turns_prepared_statements_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@pooler.example:6543/postgres")
    params = _connect_params(supabase_engine())
    assert "prepare_threshold" in params and params["prepare_threshold"] is None
    assert params["port"] == 6543


def test_postgres_engine_keeps_psycopg_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:5432/postgres")
    assert "prepare_threshold" not in _connect_params(create_engine_from_env())
