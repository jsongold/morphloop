"""ClaimStore contract suite, run against Postgres and the in-memory fake (#174).

The concurrency tests race real threads through one barrier: exactly one
claimant wins a lease, a counter never passes its limit, slots never pass cap.
Tables are shared across tests, so each test uses fresh keys.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.core.ports.claims import ClaimStore
from harness.testing.claims import InMemoryClaimStore

THREADS = 24
HOUR = timedelta(hours=1)


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Iterator[ClaimStore]:
    if request.param == "memory":
        yield InMemoryClaimStore()
        return
    # A pool as large as the race, so every thread holds its own connection.
    engine = create_engine(request.getfixturevalue("pg_url"), pool_size=THREADS)
    try:
        yield PostgresClaimStore(engine)
    finally:
        engine.dispose()


@pytest.fixture
def key() -> str:
    return f"k_{uuid.uuid4().hex}"


def race[T](n: int, fn: Callable[[int], T]) -> list[T]:
    barrier = threading.Barrier(n)
    results: list[T] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(i: int) -> None:
        barrier.wait()
        try:
            result = fn(i)
        except BaseException as exc:
            errors.append(exc)
            return
        with lock:
            results.append(result)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    return results


def test_claim_is_exclusive_until_release(store: ClaimStore, key: str) -> None:
    assert store.try_claim(key, "a", HOUR)
    assert not store.try_claim(key, "b", HOUR)
    assert store.try_claim(key, "a", HOUR)  # the holder renews
    store.release(key, "b")  # not the holder: no-op
    assert not store.try_claim(key, "b", HOUR)
    store.release(key, "a")
    assert store.try_claim(key, "b", HOUR)


def test_claim_is_free_again_after_ttl(store: ClaimStore, key: str) -> None:
    assert store.try_claim(key, "a", timedelta(milliseconds=200))
    assert not store.try_claim(key, "b", HOUR)
    time.sleep(0.3)
    assert store.try_claim(key, "b", HOUR)


def test_exactly_one_concurrent_claimant_wins(store: ClaimStore, key: str) -> None:
    wins = race(THREADS, lambda i: store.try_claim(key, f"h{i}", HOUR))
    assert wins.count(True) == 1


def test_consume_refuses_at_limit(store: ClaimStore, key: str) -> None:
    assert [store.consume(key, "calls", limit=3, window=HOUR) for _ in range(5)] == [
        True,
        True,
        True,
        False,
        False,
    ]
    assert store.consume(key, "other", limit=3, window=HOUR)
    assert store.consume(f"{key}_2", "calls", limit=3, window=HOUR)


def test_concurrent_consume_never_exceeds_limit(store: ClaimStore, key: str) -> None:
    allowed = race(THREADS, lambda _: store.consume(key, "calls", limit=5, window=HOUR))
    assert allowed.count(True) == 5
    assert not store.consume(key, "calls", limit=5, window=HOUR)


def test_consume_starts_over_in_the_next_window(store: ClaimStore, key: str) -> None:
    window = timedelta(seconds=1)
    time.sleep(1 - time.time() % 1 + 0.05)  # just past a window boundary
    assert store.consume(key, "calls", limit=1, window=window)
    assert not store.consume(key, "calls", limit=1, window=window)
    time.sleep(1)
    assert store.consume(key, "calls", limit=1, window=window)


def test_concurrent_slots_never_exceed_cap(store: ClaimStore, key: str) -> None:
    got = race(
        THREADS,
        lambda i: (f"h{i}", store.acquire_slot(key, "run", holder=f"h{i}", cap=3, ttl=HOUR)),
    )
    held = [(holder, slot) for holder, slot in got if slot is not None]
    assert len(held) == 3
    assert len({slot for _, slot in held}) == 3
    assert store.acquire_slot(key, "run", holder="late", cap=3, ttl=HOUR) is None
    holder, slot = held[0]
    store.release(slot, holder)
    assert store.acquire_slot(key, "run", holder="late", cap=3, ttl=HOUR) == slot


def test_invalid_arguments_are_refused(store: ClaimStore, key: str) -> None:
    with pytest.raises(ValueError):
        store.try_claim(key, "a", timedelta(0))
    with pytest.raises(ValueError):
        store.consume(key, "calls", limit=0, window=HOUR)
    with pytest.raises(ValueError):
        store.acquire_slot(key, "run", holder="a", cap=0, ttl=HOUR)


def test_purge_expired_deletes_expired_leases_and_old_windows(pg_url: str, key: str) -> None:
    engine = create_engine(pg_url)
    store = PostgresClaimStore(engine)
    try:
        store.try_claim(f"{key}_old", "a", timedelta(milliseconds=1))
        store.try_claim(f"{key}_live", "a", HOUR)
        store.consume(key, "calls", limit=5, window=timedelta(seconds=1))
        time.sleep(0.05)
        assert store.purge_expired(counters_older_than=timedelta(hours=2)) >= 1
        with engine.connect() as conn:
            keys = conn.execute(text("SELECT key FROM claims WHERE key LIKE :p"), {"p": f"{key}%"})
            assert [r[0] for r in keys] == [f"{key}_live"]
            assert (
                conn.execute(
                    text("SELECT count(*) FROM usage_counters WHERE subject = :s"), {"s": key}
                ).scalar_one()
                == 1
            )
        time.sleep(1.1)
        store.purge_expired(counters_older_than=timedelta(seconds=1))
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM usage_counters WHERE subject = :s"), {"s": key}
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
