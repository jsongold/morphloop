"""InMemoryClaimStore with a stepped clock: expiry, window rollover, purge (#174)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from harness.testing.claims import InMemoryClaimStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> InMemoryClaimStore:
    return InMemoryClaimStore(clock)


def test_lease_expires_after_ttl(store: InMemoryClaimStore, clock: Clock) -> None:
    assert store.try_claim("k", "a", timedelta(seconds=10))
    clock.now += timedelta(seconds=10)
    assert not store.try_claim("k", "b", timedelta(seconds=10))
    clock.now += timedelta(microseconds=1)
    assert store.try_claim("k", "b", timedelta(seconds=10))
    assert not store.try_claim("k", "a", timedelta(seconds=10))


def test_counter_resets_in_next_window(store: InMemoryClaimStore, clock: Clock) -> None:
    minute = timedelta(minutes=1)
    clock.now = T0 + timedelta(seconds=59)
    assert store.consume("u", "calls", limit=1, window=minute)
    assert not store.consume("u", "calls", limit=1, window=minute)
    clock.now = T0 + timedelta(seconds=60)
    assert store.consume("u", "calls", limit=1, window=minute)


def test_purge_expired_drops_expired_leases_and_old_windows(
    store: InMemoryClaimStore, clock: Clock
) -> None:
    store.try_claim("old", "a", timedelta(seconds=1))
    store.try_claim("live", "a", timedelta(hours=1))
    store.consume("u", "calls", limit=5, window=timedelta(minutes=1))
    clock.now += timedelta(minutes=5)
    assert store.purge_expired(counters_older_than=timedelta(minutes=2)) == 2
    assert store.try_claim("old", "b", timedelta(seconds=1))
    assert not store.try_claim("live", "b", timedelta(seconds=1))
