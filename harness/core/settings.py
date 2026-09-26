"""Environment variables the harness reads directly (pydantic-settings).

One :class:`Settings` model for the env vars the harness reads:
``MORPHLOOP_CONTRACTS_DIR``, ``DATABASE_URL``, ``WEB_ORIGIN``,
``HARNESS_VERSION``, ``MORPHLOOP_PACK_V2_DIR``, ``MORPHLOOP_USER_ID``,
``MORPHLOOP_LLM_PROVIDER`` and ``MORPHLOOP_ENVIRONMENT``.
Field names match their env var names case-insensitively (the library
default), so no prefix or alias mapping is needed.

Callers must instantiate ``Settings()`` at the point of use, not cache it at
import time: pydantic-settings reads the environment when the model is
constructed, and tests (``tests/api/test_health.py``,
``tests/adapters/postgres/conftest.py``) monkeypatch these env vars per test,
expecting the next read to see the new value.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Env vars read directly by the harness (names and defaults unchanged)."""

    morphloop_contracts_dir: str | None = None
    database_url: str = "postgresql+psycopg://morphloop:morphloop@localhost:5432/morphloop"
    web_origin: str = "http://localhost:3000"
    harness_version: str | None = None
    # v0.2.0 (#77): the v2 pack directory (no default: the harness names no pack)
    # and the single learner's id (`contracts/schemas/common/ids.json` user_id).
    morphloop_pack_v2_dir: str | None = None
    morphloop_user_id: str = Field(default="usr_local", pattern=r"^usr_[0-9A-Za-z]{1,64}$")
    # #130: "fake" swaps in a deterministic, keyless LLM for dev-stack / local E2E
    # (harness.cli.wiring.llm_provider); refused when morphloop_environment is
    # "production".
    morphloop_llm_provider: str = "litellm"
    morphloop_environment: str = "development"
