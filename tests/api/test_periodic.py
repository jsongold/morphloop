"""Run-once periodic jobs (#179): the lease makes a job run once per interval
across workers; the SDK's claims purge job deletes expired rows."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from harness.api.periodic import CLAIMS_PURGE, PeriodicJob, claims_purge, run_once
from harness.core.ports.claims import MAX_COUNTER_WINDOW
from harness.testing.claims import InMemoryClaimStore

EVERY = timedelta(minutes=5)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_one_worker_runs_each_interval_and_another_takes_over_when_it_stops() -> None:
    clock = Clock()
    store = InMemoryClaimStore(clock)
    runs: list[str] = []
    job = PeriodicJob("tick", EVERY, lambda: runs.append("ran"))

    # Three workers tick at the same moment: exactly one runs.
    assert [run_once(job, store, w) for w in ("a", "b", "c")] == [True, False, False]
    # Mid-interval ticks from other workers are refused.
    clock.now += EVERY / 2
    assert run_once(job, store, "b") is False
    # The holder renews on its next tick; the others stay refused.
    clock.now += EVERY / 2
    assert run_once(job, store, "a") is True
    assert run_once(job, store, "b") is False
    # "a" stops ticking: after its lease expires another worker takes over.
    clock.now += EVERY + timedelta(seconds=1)
    assert run_once(job, store, "b") is True
    assert runs == ["ran"] * 3


def test_run_once_renews_the_lease_while_the_job_outruns_its_interval() -> None:
    # Real wall clock (InMemoryClaimStore's default): the renewer thread ticks
    # in real time regardless of what a fake clock says.
    store = InMemoryClaimStore()
    every = timedelta(milliseconds=80)
    started = threading.Event()
    finish = threading.Event()
    job = PeriodicJob("slow", every, lambda: (started.set(), finish.wait(2)))

    runner = threading.Thread(target=run_once, args=(job, store, "a"))
    runner.start()
    assert started.wait(1)

    # Outlast the interval several times over while "a" is still running: its
    # lease keeps getting renewed, so another worker stays refused.
    assert not finish.wait(every.total_seconds() * 4)
    assert run_once(PeriodicJob("slow", every, lambda: None), store, "b") is False

    finish.set()
    runner.join(1)
    assert not runner.is_alive()

    # Once "a" stops renewing and its last lease expires, "b" can take over.
    time.sleep(every.total_seconds() * 1.5)
    assert run_once(PeriodicJob("slow", every, lambda: None), store, "b") is True


def test_claims_purge_keeps_the_longest_window_until_it_ends() -> None:
    clock = Clock()
    store = InMemoryClaimStore(clock)
    assert store.consume("u", "calls", limit=1, window=MAX_COUNTER_WINDOW) is None
    job = claims_purge(lambda: store)
    # Hourly purges during the window never reset the counter.
    for _ in range(int(MAX_COUNTER_WINDOW / timedelta(hours=1)) - 1):
        clock.now += timedelta(hours=1)
        job.run()
        assert store.consume("u", "calls", limit=1, window=MAX_COUNTER_WINDOW) is not None


def test_claims_purge_deletes_expired_leases() -> None:
    clock = Clock()
    store = InMemoryClaimStore(clock)
    store.try_claim("k", "h", timedelta(seconds=1))
    clock.now += timedelta(seconds=2)
    job = claims_purge(lambda: store)
    assert job.name == CLAIMS_PURGE
    assert run_once(job, store, "w") is True
    assert store.purge_expired(counters_older_than=timedelta(days=1)) == 0
