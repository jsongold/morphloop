"""Real Postgres 16 for the Postgres adapter tests.

Uses ``TEST_DATABASE_URL`` when set. Otherwise starts a throwaway
``postgres:16`` container on a free host port through the Docker SDK and
removes it afterwards; the tests are skipped when Docker is unavailable.
Either way the database is migrated to ``head`` with the repo's alembic setup.

The event log cannot be emptied (it is append-only by design), so tests use
fresh ids and never assume an empty store.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[3]
_IMAGE = "postgres:16"
_USER = _PASSWORD = _DB = "morphloop_test"
_READY_TIMEOUT_SECONDS = 60


def _start_container() -> tuple[Any, str]:
    try:
        import docker
        from docker.errors import DockerException
    except ImportError:  # pragma: no cover - docker is a project dependency
        pytest.skip("docker SDK not installed")
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        pytest.skip(f"Docker is unavailable: {exc}")
    container = client.containers.run(
        _IMAGE,
        detach=True,
        remove=True,
        environment={
            "POSTGRES_USER": _USER,
            "POSTGRES_PASSWORD": _PASSWORD,
            "POSTGRES_DB": _DB,
        },
        # Host port None: Docker picks a free port on loopback.
        ports={"5432/tcp": ("127.0.0.1", None)},
    )
    try:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        port = None
        while port is None:
            container.reload()
            bindings = container.attrs["NetworkSettings"]["Ports"].get("5432/tcp")
            if bindings:
                port = bindings[0]["HostPort"]
            elif time.monotonic() > deadline:
                raise RuntimeError("postgres container published no port")
            else:
                time.sleep(0.1)
        url = f"postgresql+psycopg://{_USER}:{_PASSWORD}@127.0.0.1:{port}/{_DB}"
        _wait_ready(url, deadline)
    except BaseException:
        container.stop(timeout=1)
        raise
    return container, url


def _wait_ready(url: str, deadline: float) -> None:
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception:
                # The image restarts the server once after init; retry until stable.
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    finally:
        engine.dispose()


def _migrate(url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # migrations/env.py lets DATABASE_URL override the configured URL.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)
        command.upgrade(config, "head")


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    container = None
    if not url:
        container, url = _start_container()
    try:
        _migrate(url)
        yield url
    finally:
        if container is not None:
            container.stop(timeout=5)


@pytest.fixture(scope="session")
def pg_engine(pg_url: str) -> Iterator[Engine]:
    engine = create_engine(pg_url)
    try:
        yield engine
    finally:
        engine.dispose()
