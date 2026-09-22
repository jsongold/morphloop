"""SQLAlchemy engine construction and connectivity check for Postgres.

This module is a technical adapter (ADR-0009): it holds no domain logic and
imports nothing from other adapters. It reads its connection string from the
``DATABASE_URL`` environment variable.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DATABASE_URL_ENV_VAR = "DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql+psycopg://morphloop:morphloop@localhost:5432/morphloop"

# Short connect timeout so a health check fails fast instead of hanging.
_CONNECT_TIMEOUT_SECONDS = 2


def get_database_url() -> str:
    """Return the configured database URL, falling back to the local default."""
    return os.environ.get(DATABASE_URL_ENV_VAR, DEFAULT_DATABASE_URL)


def create_engine_from_env() -> Engine:
    """Create a SQLAlchemy engine using ``DATABASE_URL`` (or the local default)."""
    return create_engine(
        get_database_url(),
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
    )


def ping(engine: Engine) -> bool:
    """Return True if a trivial query succeeds against ``engine``, else False.

    Any connection error (timeout, refused connection, auth failure, etc.) is
    caught and reported as False rather than raised.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
