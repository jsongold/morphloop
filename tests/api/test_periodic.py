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


class _FlakyRenewalStore(InMemoryClaimStore):
    """``try_claim`` raises once, on its ``fail_at``-th call, then behaves
    normally (simulates a transient DB failure during renewal, #232)."""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self._calls = 0
        self._fail_at = fail_at

    def try_claim(self, key: str, holder: str, ttl: timedelta) -> bool:
        self._calls += 1
        if self._calls == self._fail_at:
            raise RuntimeError("transient db failure")
        return super().try_claim(key, holder, ttl)


class _StallingRenewalStore(InMemoryClaimStore):
    """``try_claim`` blocks on ``stall`` on its ``stall_at``-th call (simulates
    a renewal DB call that stalls, #233)."""

    def __init__(self, stall: threading.Event, stall_at: int) -> None:
        super().__init__()
        self._calls = 0
        self._stall = stall
        self._stall_at = stall_at
        self.stalled = threading.Event()

    def try_claim(self, key: str, holder: str, ttl: timedelta) -> bool:
        self._calls += 1
        if self._calls == self._stall_at:
            self.stalled.set()
            self._stall.wait(5)
        return super().try_claim(key, holder, ttl)


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


def test_renewer_stops_renewing_a_hung_job_so_another_worker_takes_over() -> None:
    # A job.run() that never returns must not keep the renewer thread renewing
    # forever: it gives up after MAX_RENEWALS_FACTOR intervals, so the lease
    # lapses and another worker can take over even though "a" is still stuck (#231).
    store = InMemoryClaimStore()
    every = timedelta(milliseconds=80)
    finish = threading.Event()
    started = threading.Event()

    def hang() -> None:
        started.set()
        finish.wait(5)

    runner = threading.Thread(target=run_once, args=(PeriodicJob("hung", every, hang), store, "a"))
    runner.start()
    try:
        # "a" must hold the lease before "b" starts polling, or "b" could win
        # it outright without exercising the renewal deadline (#263).
        assert started.wait(2)
        deadline = time.monotonic() + 3
        took_over = False
        while time.monotonic() < deadline:
            if run_once(PeriodicJob("hung", every, lambda: None), store, "b"):
                took_over = True
                break
            time.sleep(every.total_seconds())
        assert took_over, "another worker never took over from the hung holder"
    finally:
        finish.set()
        runner.join(1)


def test_renewer_retries_after_a_try_claim_exception_instead_of_dying() -> None:
    # The first renewal attempt (the try_claim call after the initial claim)
    # raises; the renewer must keep going, not die and let the lease lapse (#232).
    every = timedelta(milliseconds=80)
    store = _FlakyRenewalStore(fail_at=2)
    finish = threading.Event()
    job = PeriodicJob("flaky", every, lambda: finish.wait(2))

    runner = threading.Thread(target=run_once, args=(job, store, "a"))
    runner.start()
    try:
        # Outlast the failing renewal and a couple of recovered ones, well
        # short of MAX_RENEWALS_FACTOR intervals.
        time.sleep(every.total_seconds() * 3)
        assert run_once(PeriodicJob("flaky", every, lambda: None), store, "b") is False
    finally:
        finish.set()
        runner.join(1)


def test_run_once_returns_even_if_a_renewal_call_stalls() -> None:
    # A renewal call stalled in the claim store must not block run_once (an
    # APScheduler job-executor thread) past a bound tied to the job's own
    # interval, or APScheduler would skip future ticks for this job (#233).
    every = timedelta(milliseconds=80)
    stall = threading.Event()
    store = _StallingRenewalStore(stall, stall_at=2)
    # The job only returns once a renewal call is demonstrably stuck (#264).
    job = PeriodicJob("stalled", every, lambda: store.stalled.wait(2))

    start = time.monotonic()
    try:
        assert run_once(job, store, "a") is True
        elapsed = time.monotonic() - start
        assert store.stalled.is_set()
        assert elapsed < 1.0
    finally:
        stall.set()


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
