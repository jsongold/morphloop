"""Tests for ``harness.adapters.postgres.migrate`` (#159)."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from harness.adapters.postgres.migrate import locate_migrations_dir, migrate
from harness.core.settings import Settings


def test_locate_migrations_dir_finds_the_checkout_s_versions() -> None:
    migrations_dir = locate_migrations_dir()

    assert migrations_dir.name == "migrations"
    assert (migrations_dir / "versions").is_dir()
    assert list((migrations_dir / "versions").glob("*.py"))


def test_migrate_against_a_real_database_is_idempotent(pg_url: str, pg_engine: Engine) -> None:
    # pg_url is already migrated to head by conftest.py; running it again must not fail
    # and must leave the same alembic_version.
    with pg_engine.connect() as conn:
        before = conn.execute(text("select version_num from alembic_version")).scalar_one()

    migrate(pg_url)

    with pg_engine.connect() as conn:
        after = conn.execute(text("select version_num from alembic_version")).scalar_one()
    assert after == before


def test_migrate_restores_the_previous_database_url_env_var(
    monkeypatch: pytest.MonkeyPatch, pg_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://placeholder/placeholder")

    migrate(pg_url)

    assert os.environ["DATABASE_URL"] == "postgresql+psycopg://placeholder/placeholder"


def test_migrate_accepts_percent_encoded_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # ConfigParser interpolation would reject a raw "%" (e.g. a URL-encoded password).
    import importlib

    migrate_mod = importlib.import_module("harness.adapters.postgres.migrate")

    seen: dict[str, str] = {}
    monkeypatch.setattr(
        migrate_mod.command,
        "upgrade",
        lambda config, rev: seen.update(url=config.get_main_option("sqlalchemy.url")),
    )
    url = "postgresql+psycopg://u:p%40ss@localhost/db"
    migrate_mod.migrate(url)
    assert seen["url"] == url


def test_v040_revision_downgrades_and_upgrades_cleanly(pg_url: str, pg_engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            command.downgrade(config, "c4e8a2d6f1b3")
            with pg_engine.connect() as conn:
                assert (
                    conn.execute(text("select to_regclass('search_embeddings')")).scalar() is None
                )
        finally:
            # pg_url may be a persistent, shared TEST_DATABASE_URL rather than a throwaway
            # container, so always restore head -- even if the assertion above fails -- to
            # avoid leaving the new v0.4 tables dropped for other test runs (#191).
            command.upgrade(config, "head")

    with pg_engine.connect() as conn:
        dims = conn.execute(
            text(
                "select format_type(atttypid, atttypmod) from pg_attribute "
                "where attrelid = 'search_embeddings'::regclass and attname = 'embedding'"
            )
        ).scalar_one()
        assert dims == f"vector({Settings().morphloop_embedding_dims})"
        tables = ("claims", "usage_counters", "search_documents")
        for table in tables:
            assert conn.execute(text("select to_regclass(:t)"), {"t": table}).scalar() == table


def test_search_owner_parent_revision_downgrades_and_upgrades(
    pg_url: str, pg_engine: Engine
) -> None:
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    columns = text(
        "select column_name from information_schema.columns "
        "where table_name = 'search_documents' and column_name in ('owner_user_id', 'parent_id')"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            command.downgrade(config, "b8d2f4a6c0e3")
            with pg_engine.connect() as conn:
                assert conn.execute(columns).scalars().all() == []
        finally:
            command.upgrade(config, "head")  # shared TEST_DATABASE_URL: always restore (#191)

    with pg_engine.connect() as conn:
        assert sorted(conn.execute(columns).scalars()) == ["owner_user_id", "parent_id"]
        assert conn.execute(text("select to_regclass('search_documents_owner_idx')")).scalar()


def test_highlight_by_position_revision_rekeys_existing_documents(
    pg_url: str, pg_engine: Engine
) -> None:
    """#220: old ``<ws_id>:<highlight_id>`` docs move to position keys with an
    id lookup; a tombstone gets its position from the created event; downgrade
    restores the old keys."""
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    ws = f"ws_{uuid.uuid4().hex}"
    active = {"highlight_id": "hl_b", "ws_id": ws, "removed": False, "position": 0}
    tombstone = {"highlight_id": "hl_a", "ws_id": ws, "removed": True}
    rows = text(
        "select key, document from view_documents_v2 "
        "where view = 'highlight' and key like :pattern order by key"
    )
    pattern = {"pattern": f"%{ws}:%"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            command.downgrade(config, "c9e1f3a5b7d0")
            with pg_engine.begin() as conn:
                # events_v2 is append-only: a fresh ws keeps reruns independent.
                removed_at = conn.execute(
                    text(
                        "insert into events_v2 (id, type, actor, user_id, ws_id, payload) "
                        "values (:id, 'highlight.created', 'learner', 'usr_local', :ws, "
                        "cast(:payload as jsonb)) returning position"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "ws": ws,
                        "payload": json.dumps({"highlight_id": "hl_a", "labels": []}),
                    },
                ).scalar_one()
                active["position"] = removed_at + 1  # distinct from the tombstone's
                for doc in (active, tombstone):
                    conn.execute(
                        text(
                            "insert into view_documents_v2 (view, key, document) "
                            "values ('highlight', :key, cast(:doc as jsonb))"
                        ),
                        {"key": f"{ws}:{doc['highlight_id']}", "doc": json.dumps(doc)},
                    )
            command.upgrade(config, "head")
            with pg_engine.connect() as conn:
                assert sorted(tuple(r) for r in conn.execute(rows, pattern)) == sorted(
                    [
                        (f"id:{ws}:hl_a", {"position": removed_at}),
                        (f"id:{ws}:hl_b", {"position": removed_at + 1}),
                        (f"{ws}:{removed_at:020d}", {**tombstone, "position": removed_at}),
                        (f"{ws}:{removed_at + 1:020d}", active),
                    ]
                )
            command.downgrade(config, "c9e1f3a5b7d0")
            with pg_engine.connect() as conn:
                assert sorted(tuple(r) for r in conn.execute(rows, pattern)) == [
                    (f"{ws}:hl_a", {**tombstone, "position": removed_at}),
                    (f"{ws}:hl_b", active),
                ]
        finally:
            command.upgrade(config, "head")  # shared TEST_DATABASE_URL: always restore (#191)
            with pg_engine.begin() as conn:
                conn.execute(
                    text(
                        "delete from view_documents_v2 "
                        "where view = 'highlight' and key like :pattern"
                    ),
                    pattern,
                )
