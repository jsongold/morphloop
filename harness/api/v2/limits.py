"""Per-user limits on `/v2` routes (#182), over the claims store (#174).

- :func:`quota` -- a dependency returning ``charge()``, which counts one use
  of ``name`` by the caller in a fixed window; past ``limit`` it refuses with
  429 ``rate-limited`` and ``Retry-After`` (seconds until the window ends).
  :class:`ChargedLLM` charges it on every LLM call; :func:`rate_limit`
  charges it once per request.
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
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

from harness.api.v2.claims import ClaimsDep
from harness.api.v2.deps import UserIdDep
from harness.core.ports.llm import LLMRequest, LLMResponse, LLMToolRequest, LLMToolResponse
from harness.core.settings import Settings

LLM = "llm"
"""The name the SDK's own LLM routes share: one per-user LLM quota."""


def rate_limited(detail: str, retry_after: int) -> HTTPException:
    """The 429 ``rate-limited`` problem, with ``Retry-After`` in whole seconds (>= 1)."""
    return HTTPException(429, detail, headers={"Retry-After": str(max(1, retry_after))})


def quota(
    name: str, *, limit: int | None = None, window: timedelta = timedelta(minutes=1)
) -> Callable[..., Callable[[], None]]:
    """A dependency returning ``charge()``: each call counts one use of ``name``
    by the caller in a fixed window, or raises 429 past ``limit``.

    Charge where the cost happens (e.g. each LLM call, via :class:`ChargedLLM`),
    so a replay or a call-free path is never refused. ``limit`` ``None`` reads
    ``MORPHLOOP_USER_LLM_CALLS_PER_MINUTE`` per request. Windows are aligned to
    the epoch (the store's rule); on refusal, ``Retry-After`` is the time left
    in the window as the store's own clock sees it (#240).
    """

    def dependency(claims: ClaimsDep, user_id: UserIdDep) -> Callable[[], None]:
        cap = limit if limit is not None else Settings().morphloop_user_llm_calls_per_minute

        def charge() -> None:
            left = claims.consume(user_id, name, limit=cap, window=window)
            if not left:
                raise rate_limited(f"more than {cap} {name} calls per {window}", math.ceil(left))

        return charge

    return dependency


def rate_limit(
    name: str, *, limit: int | None = None, window: timedelta = timedelta(minutes=1)
) -> Callable[..., None]:
    """A dependency charging one use of ``name`` per request (see :func:`quota`)."""
    charger = quota(name, limit=limit, window=window)

    def dependency(claims: ClaimsDep, user_id: UserIdDep) -> None:
        charger(claims, user_id)()

    return dependency


class ChargedLLM:
    """An LLM provider that charges ``charge()`` before every call it forwards.

    Wraps an ``LLMProvider`` or ``LLMToolProvider``; only the method the
    wrapped provider has is usable.
    """

    def __init__(self, provider: Any, charge: Callable[[], None]) -> None:
        self.provider = provider
        self.charge = charge

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.charge()
        response: LLMResponse = self.provider.complete_structured(request)
        return response

    def complete_with_tools(self, request: LLMToolRequest) -> LLMToolResponse:
        self.charge()
        response: LLMToolResponse = self.provider.complete_with_tools(request)
        return response


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
