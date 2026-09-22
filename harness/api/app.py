"""FastAPI application factory (Slice 0).

`api` is the wiring layer: it may import `core` and `adapters`, but holds no
domain logic itself. Slice 0 exposes a single health endpoint that reports
liveness and DB connectivity.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from harness.adapters.postgres.engine import create_engine_from_env, ping

WEB_ORIGIN_ENV_VAR = "WEB_ORIGIN"
DEFAULT_WEB_ORIGIN = "http://localhost:3000"


def check_db() -> bool:
    """Default DB health check: ping a fresh engine built from DATABASE_URL.

    This is a FastAPI dependency, so tests can replace it via
    ``app.dependency_overrides[check_db] = ...`` without touching a real
    database.
    """
    engine = create_engine_from_env()
    try:
        return ping(engine)
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="morphloop-api")

    web_origin = os.environ.get(WEB_ORIGIN_ENV_VAR, DEFAULT_WEB_ORIGIN)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health(db_ok: bool = Depends(check_db)) -> dict[str, str]:
        return {"status": "ok", "db": "ok" if db_ok else "down"}

    return app


app = create_app()
