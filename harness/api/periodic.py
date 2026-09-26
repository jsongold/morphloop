"""Run-once periodic jobs across API workers (#179).

Every worker starts an APScheduler 3 ``AsyncIOScheduler`` in the app lifespan;
a sync job runs in the event loop's default thread pool
(``apscheduler/executors/asyncio.py``), with ``max_instances=1`` and
``coalesce=True`` so a slow tick never overlaps itself in one worker.

APScheduler 3 has no cross-process run-once, so each tick first takes the
claims lease ``periodic:<name>`` (TTL = the job's interval, never released).
The worker that holds it renews it on its next tick; the others are refused
until it expires, so the job runs once per interval however many workers there
are, and another worker takes over when the holder dies.

An app chooses its jobs with ``AppExtension(periodic_jobs=...)``. The SDK adds
one, :data:`CLAIMS_PURGE`; an app job with the same name replaces it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.adapters.postgres.engine import create_engine_from_env
from harness.core.ports.claims import MAX_COUNTER_WINDOW, ClaimStore, check_positive

CLAIMS_PURGE = "claims-purge"
PURGE_EVERY = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    """``run`` (sync, no arguments) once every ``every`` across all workers."""

    name: str
    every: timedelta
    run: Callable[[], object]


def app_claims(app: FastAPI) -> ClaimStore:
    """``app.state.claims``, or Postgres built and cached there (as ``ClaimsDep``)."""
    store: ClaimStore | None = getattr(app.state, "claims", None)
    if store is None:
        store = PostgresClaimStore(create_engine_from_env())
        app.state.claims = store
    return store


def run_once(job: PeriodicJob, claims: ClaimStore, holder: str) -> bool:
    """Run ``job`` if ``holder`` wins (or renews) its lease; ``True`` if it ran.

    ponytail: a job slower than its interval can overlap on another worker
    once the lease expires; renew the lease while running if that matters.
    """
    if not claims.try_claim(f"periodic:{job.name}", holder, job.every):
        return False
    job.run()
    return True


def claims_purge(store_of: Callable[[], ClaimStore]) -> PeriodicJob:
    """The SDK's job: delete expired leases and counter windows that have ended
    (a window is at most ``MAX_COUNTER_WINDOW`` long, so older rows are over)."""
    return PeriodicJob(
        CLAIMS_PURGE,
        PURGE_EVERY,
        lambda: store_of().purge_expired(counters_older_than=MAX_COUNTER_WINDOW),
    )


def start_scheduler(
    jobs: Iterable[PeriodicJob], store_of: Callable[[], ClaimStore]
) -> AsyncIOScheduler:
    """Start (on the running loop) a scheduler ticking ``jobs``; last job per name wins."""
    by_name = {job.name: job for job in jobs}
    holder = uuid.uuid4().hex
    scheduler = AsyncIOScheduler()
    for job in by_name.values():
        check_positive(every=job.every.total_seconds())
        scheduler.add_job(
            lambda job=job: run_once(job, store_of(), holder),
            "interval",
            seconds=job.every.total_seconds(),
            id=job.name,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    return scheduler
