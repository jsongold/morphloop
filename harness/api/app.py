"""FastAPI application factory.

`api` is the wiring layer: it may import `core` and `adapters`, but holds no
domain logic itself (ADR-0017). The endpoints are in :mod:`harness.api.routes`
and :mod:`harness.api.terminal`, the composition root in
:mod:`harness.api.backend`, and every error body in :mod:`harness.api.problems`.

`/health` deliberately does not depend on the wired backend: it stays answerable
when the database or the Docker daemon is down, which is exactly when someone
asks it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from harness.adapters.postgres.engine import create_engine_from_env, ping
from harness.api import routes, terminal
from harness.api.backend import Backend, build_backend
from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.core.settings import Settings

WEB_ORIGIN_ENV_VAR = "WEB_ORIGIN"
DEFAULT_WEB_ORIGIN = "http://localhost:3000"

logger = logging.getLogger(__name__)


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


def create_app(backend: Backend | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``backend`` is the wired loop; when it is omitted it is built from the
    environment at startup (``DATABASE_URL``, the Docker daemon, the LLM keys
    litellm reads itself). A failure to build it is logged rather than raised,
    so ``/health`` still reports.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.backend = backend
        app.state.terminals = terminal.TerminalRegistry()
        if backend is None:
            try:
                app.state.backend = build_backend()
            except Exception:
                logger.exception("could not wire the backend; only /health will answer")
        yield

    app = FastAPI(title="morphloop-api", lifespan=lifespan)

    web_origin = Settings().web_origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[routes.REPLAYED_HEADER],
    )

    @app.get("/health")
    def health(db_ok: bool = Depends(check_db)) -> dict[str, str]:
        return {"status": "ok", "db": "ok" if db_ok else "down"}

    install_handlers(app)
    app.include_router(routes.router)
    app.include_router(terminal.router)
    app.include_router(build_v2_router())
    return app


app = create_app()
