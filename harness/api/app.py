"""FastAPI application factory.

`api` is the wiring layer: it may import `core` and `adapters`, but holds no
domain logic itself (ADR-0017). The endpoints are the `/v2` resource routes
(:mod:`harness.api.v2`), and every error body is in :mod:`harness.api.problems`.

`/health` deliberately does not depend on anything else: it stays answerable
when the database or the Docker daemon is down, which is exactly when someone
asks it.

An app built on the SDK (its own repository, ADR-0018 §19) assembles its server
with ``create_app(extensions=[AppExtension(...)])``: each extension contributes
routers mounted under ``/v2`` and the :class:`Artifact` types its packs may use.
The SDK itself registers no artifact type. ``periodic_jobs`` run once per
interval across workers (:mod:`harness.api.periodic`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from harness.adapters.postgres.engine import ping
from harness.api.periodic import PeriodicJob, app_claims, claims_purge, start_scheduler
from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.api.v2.auth import auth_provider_from_settings
from harness.api.v2.db import DbProvider, db_of
from harness.api.v2.deps import user_id_of
from harness.core.artifact import Artifact
from harness.core.ports.auth import AuthProvider
from harness.core.settings import Settings

REPLAYED_HEADER = "Idempotent-Replayed"


def check_db(request: Request) -> bool:
    """Default DB health check: ping a fresh engine from the app's DB provider.

    This is a FastAPI dependency, so tests can replace it via
    ``app.dependency_overrides[check_db] = ...`` without touching a real
    database.
    """
    engine = db_of(request)()
    try:
        return ping(engine)
    finally:
        engine.dispose()


@dataclass(frozen=True, slots=True)
class AppExtension:
    """What one app adds to the SDK server (#95).

    ``routers`` are mounted under ``/v2``; ``artifact_types`` are the
    :class:`Artifact` subclasses the pack importer accepts (``PackV2Dep``);
    ``periodic_jobs`` start and stop with the app (:mod:`harness.api.periodic`).
    """

    routers: tuple[APIRouter, ...] = ()
    artifact_types: tuple[type[Artifact], ...] = ()
    periodic_jobs: tuple[PeriodicJob, ...] = ()


def create_app(
    *,
    extensions: Iterable[AppExtension] = (),
    auth: AuthProvider | None = None,
    db: DbProvider | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    ``extensions`` are an app's :class:`AppExtension` values. Without them the
    server registers no artifact type, so a pack that embeds artifacts is refused.
    ``auth`` is the app's :class:`AuthProvider` (e.g. ``OidcAuthProvider`` or
    ``supabase_auth(...)``); unset, ``MORPHLOOP_AUTH_PROVIDER`` picks one.
    ``db`` is the app's engine factory (``create_engine_from_env`` or
    ``supabase_engine``); unset, ``MORPHLOOP_DB_PROVIDER`` picks one.
    """
    extensions = tuple(extensions)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Resolve the provider at startup, not at import (the module-level
        # `app` below must stay importable): production with the dev provider
        # or an incomplete oidc/supabase config refuses to start (#171).
        if getattr(app.state, "auth_provider", None) is None:
            app.state.auth_provider = auth_provider_from_settings()
        store_of = partial(app_claims, app)
        jobs = (claims_purge(store_of), *(j for e in extensions for j in e.periodic_jobs))
        app.state.scheduler = start_scheduler(jobs, store_of)
        try:
            yield
        finally:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="morphloop-api", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=Settings().cors_origins,
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
    # Every /v2 operation needs the bearer token (the contract's root
    # `security`). As a router dependency it runs before a route's DB/pack
    # dependencies; the route's own UserIdDep reuses the cached result (#171).
    app.include_router(v2, dependencies=[Depends(user_id_of)])
    if auth is not None:
        app.state.auth_provider = auth
    if db is not None:
        app.state.db = db
    app.state.artifact_types = tuple(t for e in extensions for t in e.artifact_types)
    return app


app = create_app()
