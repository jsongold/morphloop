"""Per-user limits on `/v2` routes (#182), over the claims store (#174).

- :func:`rate_limit` -- a dependency counting the caller's calls to ``name``
  in a fixed window; past ``limit`` it refuses with 429 ``rate-limited`` and
  ``Retry-After`` (seconds until the window ends).
- :func:`concurrency_slot` -- a dependency holding one of ``cap`` slots of
  ``name`` for the caller while the request runs; 429 when all are held.
  An app puts it on the routes that start its costly work (e.g. its labs).

The app chooses: pass ``limit``/``cap`` when building the dependency, or leave
them ``None`` to read ``MORPHLOOP_USER_LLM_CALLS_PER_MINUTE`` /
``MORPHLOOP_USER_MAX_CONCURRENT_LABS`` per request. Names are opaque labels
(ADR-0018). Counters and slots live in the wired ``ClaimStore``, so limits hold
across API processes.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta

from fastapi import HTTPException

from harness.api.v2.claims import ClaimsDep
from harness.api.v2.deps import UserIdDep
from harness.core.settings import Settings

LLM = "llm"
"""The name the SDK's own LLM routes share: one per-user LLM quota."""


def rate_limited(detail: str, retry_after: int) -> HTTPException:
    """The 429 ``rate-limited`` problem, with ``Retry-After`` in whole seconds (>= 1)."""
    return HTTPException(429, detail, headers={"Retry-After": str(max(1, retry_after))})


def rate_limit(
    name: str, *, limit: int | None = None, window: timedelta = timedelta(minutes=1)
) -> Callable[..., None]:
    """A dependency: count one call of the caller to ``name`` or raise 429.

    ``limit`` ``None`` reads ``MORPHLOOP_USER_LLM_CALLS_PER_MINUTE`` per request.
    Windows are aligned to the epoch (the store's rule), so ``Retry-After`` is
    the time left in the current one.
    """
    seconds = window.total_seconds()

    def dependency(claims: ClaimsDep, user_id: UserIdDep) -> None:
        cap = limit if limit is not None else Settings().morphloop_user_llm_calls_per_minute
        if not claims.consume(user_id, name, limit=cap, window=window):
            left = seconds - time.time() % seconds
            raise rate_limited(f"more than {cap} {name} calls per {window}", math.ceil(left))

    return dependency


def concurrency_slot(
    name: str,
    *,
    cap: int | None = None,
    ttl: timedelta = timedelta(minutes=10),
    retry_after: int = 5,
) -> Callable[..., Iterator[str]]:
    """A dependency holding one of ``cap`` slots of ``name`` for the caller.

    Yields the slot key and releases it once the response is sent. ``cap``
    ``None`` reads ``MORPHLOOP_USER_MAX_CONCURRENT_LABS`` per request. ``ttl``
    bounds a slot a crashed worker never released; it must outlast the
    request. All held: 429 with ``Retry-After: retry_after``.
    """

    def dependency(claims: ClaimsDep, user_id: UserIdDep) -> Iterator[str]:
        slots = cap if cap is not None else Settings().morphloop_user_max_concurrent_labs
        holder = uuid.uuid4().hex
        key = claims.acquire_slot(user_id, name, holder=holder, cap=slots, ttl=ttl)
        if key is None:
            raise rate_limited(f"{slots} concurrent {name} already running", retry_after)
        try:
            yield key
        finally:
            claims.release(key, holder)

    return dependency
