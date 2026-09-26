"""Choosing the DB provider (#186): the SDK offers, the app chooses.

A DB provider is an engine factory (``create_engine_from_env`` for ``postgres``,
``supabase_engine`` for ``supabase``). ``create_app(db=...)`` stores the app's
factory on ``app.state.db`` and it wins; otherwise ``MORPHLOOP_DB_PROVIDER``
(``postgres`` by default) picks one.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from sqlalchemy.engine import Engine

from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.supabase import supabase_engine
from harness.core.settings import Settings

DbProvider = Callable[[], Engine]


def db_from_settings() -> DbProvider:
    if Settings().morphloop_db_provider == "supabase":
        return supabase_engine
    return create_engine_from_env


def db_of(request: Request) -> DbProvider:
    provider: DbProvider | None = getattr(request.app.state, "db", None)
    return provider if provider is not None else db_from_settings()
