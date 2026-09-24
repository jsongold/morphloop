"""Environment variables the harness reads directly (pydantic-settings).

One :class:`Settings` model for the four env vars previously read ad hoc via
``os.environ.get``: ``MORPHLOOP_CONTRACTS_DIR``, ``DATABASE_URL``,
``WEB_ORIGIN`` and ``HARNESS_VERSION``. Field names match their env var names
case-insensitively (the library default), so no prefix or alias mapping is
needed.

Callers must instantiate ``Settings()`` at the point of use, not cache it at
import time: pydantic-settings reads the environment when the model is
constructed, and tests (``tests/api/test_health.py``,
``tests/adapters/postgres/conftest.py``) monkeypatch these env vars per test,
expecting the next read to see the new value.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Env vars read directly by the harness (names and defaults unchanged)."""

    morphloop_contracts_dir: str | None = None
    database_url: str = "postgresql+psycopg://morphloop:morphloop@localhost:5432/morphloop"
    web_origin: str = "http://localhost:3000"
    harness_version: str | None = None
