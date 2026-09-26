"""Tests for ``harness.adapters.postgres.migrate`` (#159)."""

from __future__ import annotations

import os

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
