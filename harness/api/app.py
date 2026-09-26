"""FastAPI application factory.

`api` is the wiring layer: it may import `core` and `adapters`, but holds no
domain logic itself (ADR-0017). The endpoints are the `/v2` resource routes
(:mod:`harness.api.v2`), and every error body is in :mod:`harness.api.problems`.

`/health` deliberately does not depend on anything else: it stays answerable
when the database or the Docker daemon is down, which is exactly when someone
asks it.

An app built on the SDK (``apps/<app>/``, ADR-0018 §19) assembles its server
with ``create_app(extensions=[AppExtension(...)])``: each extension contributes
routers mounted under ``/v2`` and the :class:`Artifact` types its packs may use.
The SDK itself registers no artifact type.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from harness.adapters.postgres.engine import create_engine_from_env, ping
from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.core.artifact import Artifact
from harness.core.settings import Settings

REPLAYED_HEADER = "Idempotent-Replayed"


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


@dataclass(frozen=True, slots=True)
class AppExtension:
    """What one app adds to the SDK server (#95).

    ``routers`` are mounted under ``/v2``; ``artifact_types`` are the
    :class:`Artifact` subclasses the pack importer accepts (``PackV2Dep``).
    """

    routers: tuple[APIRouter, ...] = ()
    artifact_types: tuple[type[Artifact], ...] = ()


def create_app(*, extensions: Iterable[AppExtension] = ()) -> FastAPI:
    """Build the FastAPI application.

    ``extensions`` are an app's :class:`AppExtension` values. Without them the
    server registers no artifact type, so a pack that embeds artifacts is refused.
    """
    extensions = tuple(extensions)
    app = FastAPI(title="morphloop-api")

    web_origin = Settings().web_origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REPLAYED_HEADER],
    )

    @app.get("/health")
    def health(db_ok: bool = Depends(check_db)) -> dict[str, str]:
        return {"status": "ok", "db": "ok" if db_ok else "down"}

    install_handlers(app)
    v2 = build_v2_router()
    for extension in extensions:
        for router in extension.routers:
            v2.include_router(router)
    app.include_router(v2)
    app.state.artifact_types = tuple(t for e in extensions for t in e.artifact_types)
    return app


app = create_app()
