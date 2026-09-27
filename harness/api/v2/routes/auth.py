"""`POST /v2/auth/socket-tickets`: trade the bearer token for a WebSocket ticket (#172).

429 `rate-limited` past `MORPHLOOP_USER_SOCKET_TICKETS_PER_MINUTE` issues in a
minute (#197), via `harness.api.v2.limits.rate_limit`. The limit is read fresh
per request (not baked in at import time) so the wrapper below reads
`Settings()` itself and calls `rate_limit`'s built dependency directly --
`rate_limit(name)`'s own `limit=None` default is reserved for the LLM quota.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from harness.api.v2.claims import ClaimsDep
from harness.api.v2.deps import UserIdDep
from harness.api.v2.limits import rate_limit
from harness.api.v2.tickets import SocketTickets, socket_tickets_of
from harness.core.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])

TICKET_ISSUE = "socket-ticket-issue"


def _ticket_issue_limit(claims: ClaimsDep, user_id: UserIdDep) -> None:
    limit = Settings().morphloop_user_socket_tickets_per_minute
    rate_limit(TICKET_ISSUE, limit=limit)(claims, user_id)


@router.post("/socket-tickets", status_code=201, dependencies=[Depends(_ticket_issue_limit)])
def create_socket_ticket(
    user_id: UserIdDep, tickets: Annotated[SocketTickets, Depends(socket_tickets_of)]
) -> dict[str, str]:
    ticket, expires_at = tickets.issue(user_id)
    return {"ticket": ticket, "expires_at": expires_at.isoformat().replace("+00:00", "Z")}
