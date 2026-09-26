"""Run-once periodic jobs (#179): the lease makes a job run once per interval
across workers; the SDK's claims purge job deletes expired rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from harness.api.periodic import CLAIMS_PURGE, PeriodicJob, claims_purge, run_once
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


def test_claims_purge_deletes_expired_leases() -> None:
    clock = Clock()
    store = InMemoryClaimStore(clock)
    store.try_claim("k", "h", timedelta(seconds=1))
    clock.now += timedelta(seconds=2)
    job = claims_purge(lambda: store)
    assert job.name == CLAIMS_PURGE
    assert run_once(job, store, "w") is True
    assert store.purge_expired(counters_older_than=timedelta(days=1)) == 0
