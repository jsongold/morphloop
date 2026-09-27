"""PostgreSQL implementation of the ClaimStore Port (#174).

Tables ``claims`` and ``usage_counters`` from migration ``d7b1e3f5a9c2``.
Each operation is one atomic statement, so concurrent callers need no lock:

- lease: ``INSERT ... ON CONFLICT (key) DO UPDATE ... WHERE expired OR same
  holder RETURNING`` -- a row comes back only for the winner.
- counter: ``INSERT ... ON CONFLICT DO UPDATE SET n = n + 1 WHERE n < :limit
  RETURNING`` -- under READ COMMITTED the WHERE is re-checked on the latest
  row version, so ``n`` never passes ``limit``.

Hand-rolled on purpose (#174 "Library considered"): pyrate-limiter's
PostgresBucket is one table per bucket with a table lock, ``limits`` has no
SQL storage, and procrastinate locks are job-queue locks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import Engine

from harness.core.ports.claims import ClaimStore, check_positive, check_window, slot_key

_CLAIM = text(
    """
    INSERT INTO claims (key, holder, expires_at)
    VALUES (:key, :holder, clock_timestamp() + :ttl)
    ON CONFLICT (key) DO UPDATE SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at
    WHERE claims.expires_at < clock_timestamp() OR claims.holder = EXCLUDED.holder
    RETURNING key
    """
)
_RELEASE = text("DELETE FROM claims WHERE key = :key AND holder = :holder")
_CONSUME = text(
    """
    INSERT INTO usage_counters (subject, name, window_start, n)
    VALUES (
        :subject, :name,
        to_timestamp(floor(extract(epoch FROM clock_timestamp()) / :secs) * :secs), 1
    )
    ON CONFLICT (subject, name, window_start) DO UPDATE SET n = usage_counters.n + 1
    WHERE usage_counters.n < :limit
    RETURNING n
    """
)
_PURGE_CLAIMS = text("DELETE FROM claims WHERE expires_at < clock_timestamp()")
_PURGE_COUNTERS = text("DELETE FROM usage_counters WHERE window_start < clock_timestamp() - :age")


class PostgresClaimStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def try_claim(self, key: str, holder: str, ttl: timedelta) -> bool:
        check_positive(ttl=ttl.total_seconds())
        with self._engine.begin() as conn:
            row = conn.execute(_CLAIM, {"key": key, "holder": holder, "ttl": ttl}).one_or_none()
        return row is not None

    def release(self, key: str, holder: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(_RELEASE, {"key": key, "holder": holder})

    def consume(self, subject: str, name: str, *, limit: int, window: timedelta) -> bool:
        secs = check_window(window)
        check_positive(limit=limit)
        params = {"subject": subject, "name": name, "limit": limit, "secs": secs}
        with self._engine.begin() as conn:
            return conn.execute(_CONSUME, params).one_or_none() is not None

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
        with self._engine.begin() as conn:
            claims = conn.execute(_PURGE_CLAIMS).rowcount
            counters = conn.execute(_PURGE_COUNTERS, {"age": counters_older_than}).rowcount
        return claims + counters


if TYPE_CHECKING:

    def _conforms(engine: Engine) -> ClaimStore:
        return PostgresClaimStore(engine)
