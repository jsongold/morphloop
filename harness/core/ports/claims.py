"""Claims Port: lease rows, fixed-window counters and concurrency slots (#174).

Single-flight and per-subject limits that hold across API processes (a
process-local set breaks with more than one worker). Keys, subjects and names
are opaque labels chosen by the caller; the SDK names no domain noun (ADR-0018).

- A *claim* is a lease: ``key`` is held by one ``holder`` until it is released
  or its ``ttl`` runs out. The holder may renew its own lease.
- A *counter* counts calls per ``(subject, name)`` in fixed windows aligned to
  the epoch (``window`` long, at most :data:`MAX_COUNTER_WINDOW`) and refuses
  once ``limit`` is reached.
- A *slot* is one of ``cap`` leases ``slot:<subject>:<name>:<i>``, so at most
  ``cap`` holders run at once per ``(subject, name)``.

Times come from the store's own clock (the database clock for Postgres), so
every process agrees on expiry.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

# The longest counter window. A purge with ``counters_older_than`` of at least
# this never deletes the row of a window that is still running (the periodic
# claims purge uses exactly this).
MAX_COUNTER_WINDOW = timedelta(days=7)


def slot_key(subject: str, name: str, index: int) -> str:
    """The claim key of slot ``index`` of ``(subject, name)``."""
    return f"slot:{subject}:{name}:{index}"


def check_positive(**values: float) -> None:
    """Raise ``ValueError`` unless every value is positive (ttl, limit, window, cap)."""
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")


def check_window(window: timedelta) -> float:
    """``window`` in seconds; ``ValueError`` unless ``0 < window <= MAX_COUNTER_WINDOW``."""
    check_positive(window=window.total_seconds())
    if window > MAX_COUNTER_WINDOW:
        raise ValueError(f"window must be at most {MAX_COUNTER_WINDOW}, got {window}")
    return window.total_seconds()


class ClaimStore(Protocol):
    """Cross-process leases and counters."""

    def try_claim(self, key: str, holder: str, ttl: timedelta) -> bool:
        """Take (or renew, if ``holder`` already has it) the lease on ``key`` for
        ``ttl``. ``False`` while another holder's lease is unexpired."""
        ...

    def release(self, key: str, holder: str) -> None:
        """Drop ``holder``'s lease on ``key``; a no-op if it holds none."""
        ...

    def consume(self, subject: str, name: str, *, limit: int, window: timedelta) -> bool:
        """Count one call in the current window; ``False`` (not counted) once
        ``limit`` calls are already counted there. ``ValueError`` if ``window``
        exceeds :data:`MAX_COUNTER_WINDOW`."""
        ...

    def acquire_slot(
        self, subject: str, name: str, *, holder: str, cap: int, ttl: timedelta
    ) -> str | None:
        """Lease the first free of ``cap`` slots and return its key (pass it to
        :meth:`release`), or ``None`` when all are held. Use a fresh ``holder``
        per acquisition: a holder renews a slot it already has."""
        ...

    def purge_expired(self, *, counters_older_than: timedelta) -> int:
        """Delete expired leases and counter windows that started more than
        ``counters_older_than`` ago; return the number of rows deleted."""
        ...
