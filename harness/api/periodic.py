"""Run-once periodic jobs across API workers (#179).

Every worker starts an APScheduler 3 ``AsyncIOScheduler`` in the app lifespan;
a sync job runs in the event loop's default thread pool
(``apscheduler/executors/asyncio.py``), with ``max_instances=1`` and
``coalesce=True`` so a slow tick never overlaps itself in one worker.

APScheduler 3 has no cross-process run-once, so each tick first takes the
claims lease ``periodic:<name>`` (TTL = the job's interval, never released).
The worker that holds it renews it on its next tick, and also from a
background thread every half interval while ``job.run()`` is in progress, so a
run that outlasts the interval keeps the lease instead of another worker's
``try_claim`` winning it mid-run (#211). The others are refused until the
lease expires, so the job runs once per interval however many workers there
are, and another worker takes over when the holder dies (or hangs).

That renewer thread is bounded three ways: it gives up after
``MAX_RENEWALS_FACTOR`` intervals so a ``job.run()`` that hangs forever
eventually loses exclusivity instead of renewing forever (#231); each renewal
attempt runs in its own short-lived daemon thread, at most one in flight, so
a ``try_claim`` call that stalls or raises (e.g. a transient DB error) is
logged and left behind rather than wedging the renewer loop itself -- the
deadline/stop checks above keep running on schedule instead of getting stuck
waiting on that one call (#232); and ``run_once`` only waits up to
``MAX_RENEWER_JOIN`` (capped by the job's own interval) for the renewer loop
to notice it should stop, so a stalled renewal can't block the caller -- an
APScheduler job-executor thread -- and make APScheduler skip future ticks
(#233).

An app chooses its jobs with ``AppExtension(periodic_jobs=...)``. The SDK adds
one, :data:`CLAIMS_PURGE`; an app job with the same name replaces it.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.api.v2.db import app_db
from harness.core.ports.claims import MAX_COUNTER_WINDOW, ClaimStore, check_positive

logger = logging.getLogger(__name__)

CLAIMS_PURGE = "claims-purge"
PURGE_EVERY = timedelta(hours=1)

# A run may keep renewing for at most this many multiples of its own interval;
# past that the renewer stops even if `job.run()` hasn't (#231).
MAX_RENEWALS_FACTOR = 5
# The longest run_once waits for the renewer thread to notice `stop` and exit,
# capped by the job's own interval so a stalled renewal call can't block the
# caller past that (#233).
MAX_RENEWER_JOIN = timedelta(seconds=5)


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
        store = PostgresClaimStore(app_db(app)())
        app.state.claims = store
    return store


def run_once(job: PeriodicJob, claims: ClaimStore, holder: str) -> bool:
    """Run ``job`` if ``holder`` wins (or renews) its lease; ``True`` if it ran.

    A background thread renews the lease every ``job.every / 2`` while
    ``job.run()`` is in progress, so a run slower than the interval keeps the
    lease instead of letting another worker's ``try_claim`` win it mid-run
    (#211) -- but bounded: see the module docstring for #231/#232/#233.
    """
    key = f"periodic:{job.name}"
    if not claims.try_claim(key, holder, job.every):
        return False
    stop = threading.Event()
    renewer = threading.Thread(
        target=_renew_until_stopped,
        args=(claims, key, holder, job.every, stop),
        daemon=True,
    )
    renewer.start()
    try:
        job.run()
    finally:
        stop.set()
        renewer.join(min(job.every, MAX_RENEWER_JOIN).total_seconds())
    return True


def _renew_until_stopped(
    claims: ClaimStore, key: str, holder: str, every: timedelta, stop: threading.Event
) -> None:
    """Re-claim ``key`` every half interval until ``stop`` is set, or until the
    run has outlasted ``MAX_RENEWALS_FACTOR`` intervals (#231; the deadline is
    rechecked after each wait so a slow cycle can't sneak one more renewal
    past it). Each attempt is handed to :func:`_attempt_renewal` in its own
    daemon thread rather than called inline, and a new one is only started
    once the last is done -- so a ``try_claim`` that stalls (e.g. a DB hang)
    can't wedge this loop and stop it from noticing ``stop`` or the deadline
    (#232, #233)."""
    deadline = time.monotonic() + every.total_seconds() * MAX_RENEWALS_FACTOR
    half = every.total_seconds() / 2
    attempt_done = threading.Event()
    attempt_done.set()
    while True:
        if time.monotonic() >= deadline:
            return
        if stop.wait(half):
            return
        if time.monotonic() >= deadline:
            return
        if attempt_done.is_set():
            attempt_done.clear()
            threading.Thread(
                target=_attempt_renewal,
                args=(claims, key, holder, every, attempt_done),
                daemon=True,
            ).start()
        # else: the previous attempt is still stuck (e.g. a stalled DB call
        # holding a pooled connection); skip this cycle rather than start a
        # second one on top of it.
        # ponytail: no query timeout on the DB call itself, so a permanently
        # stuck attempt still leaks one connection for the process lifetime.
        # Upgrade path: a statement_timeout on the claims engine (adapter
        # layer, out of scope here).


def _attempt_renewal(
    claims: ClaimStore, key: str, holder: str, every: timedelta, done: threading.Event
) -> None:
    """One renewal attempt; ``done`` is set on success, refusal or failure so
    :func:`_renew_until_stopped` knows when it's safe to start the next one.
    A failure (e.g. a transient DB error) is logged here rather than left to
    propagate and kill the calling thread (#232)."""
    try:
        claims.try_claim(key, holder, every)
    except Exception:
        logger.warning("periodic: lease renewal failed for %s, retrying", key, exc_info=True)
    finally:
        done.set()


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
