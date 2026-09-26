"""`POST /v2/auth/socket-tickets`: trade the bearer token for a WebSocket ticket (#172)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from harness.api.v2.deps import UserIdDep
from harness.api.v2.tickets import SocketTickets, socket_tickets_of

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/socket-tickets", status_code=201)
def create_socket_ticket(
    user_id: UserIdDep, tickets: Annotated[SocketTickets, Depends(socket_tickets_of)]
) -> dict[str, str]:
    ticket, expires_at = tickets.issue(user_id)
    return {"ticket": ticket, "expires_at": expires_at.isoformat().replace("+00:00", "Z")}
