"""`ClaimsDep`: the wired :class:`~harness.core.ports.claims.ClaimStore` (#174).

Postgres by default, built and cached on ``app.state.claims`` on first use, so
leases and limits hold across API processes. An app (or a test) sets
``app.state.claims`` before the first request to choose another store, e.g.
``harness.testing.claims.InMemoryClaimStore`` for a single process.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.api.v2.db import db_of
from harness.core.ports.claims import ClaimStore


def claims_of(request: Request) -> ClaimStore:
    """The wired store; Postgres, built and cached on `app.state` on first use."""
    store: ClaimStore | None = getattr(request.app.state, "claims", None)
    if store is None:
        store = PostgresClaimStore(db_of(request)())
        request.app.state.claims = store
    return store


ClaimsDep = Annotated[ClaimStore, Depends(claims_of)]
