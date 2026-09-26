"""In-memory :class:`~harness.core.ports.claims.ClaimStore` (one process only).

Same contract as the Postgres adapter (``tests/adapters/postgres/test_claims.py``
runs one suite against both). ``clock`` defaults to the wall clock; tests pass
their own to step time.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from harness.core.ports.claims import ClaimStore, check_positive, slot_key


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryClaimStore:
    def __init__(self, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._counters: dict[tuple[str, str, datetime], int] = {}
        self._lock = threading.Lock()

    def try_claim(self, key: str, holder: str, ttl: timedelta) -> bool:
        check_positive(ttl=ttl.total_seconds())
        with self._lock:
            now = self._clock()
            current = self._leases.get(key)
            if current is not None and current[0] != holder and current[1] >= now:
                return False
            self._leases[key] = (holder, now + ttl)
            return True

    def release(self, key: str, holder: str) -> None:
        """A no-op if ``key`` has no lease, or its holder differs (#206)."""
        with self._lock:
            current = self._leases.get(key)
            if current is not None and current[0] == holder:
                del self._leases[key]

    def consume(self, subject: str, name: str, *, limit: int, window: timedelta) -> bool:
        secs = window.total_seconds()
        check_positive(limit=limit, window=secs)
        with self._lock:
            epoch = self._clock().timestamp()
            start = datetime.fromtimestamp(epoch // secs * secs, UTC)
            key = (subject, name, start)
            n = self._counters.get(key, 0)
            if n >= limit:
                return False
            self._counters[key] = n + 1
            return True

    def acquire_slot(
        self, subject: str, name: str, *, holder: str, cap: int, ttl: timedelta
    ) -> str | None:
        check_positive(cap=cap)
        for index in range(cap):
            key = slot_key(subject, name, index)
            if self.try_claim(key, holder, ttl):
                return key
        return None

    def purge_expired(self, *, counters_older_than: timedelta) -> int:
        with self._lock:
            now = self._clock()
            leases = [k for k, (_, expires) in self._leases.items() if expires < now]
            counters = [k for k in self._counters if k[2] < now - counters_older_than]
            for key in leases:
                del self._leases[key]
            for counter in counters:
                del self._counters[counter]
        return len(leases) + len(counters)


if TYPE_CHECKING:

    def _conforms() -> ClaimStore:
        return InMemoryClaimStore()
