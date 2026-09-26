"""Short-lived, single-use WebSocket tickets (#172).

A browser cannot set ``Authorization`` on a WebSocket upgrade, so the client
trades its bearer token for a ticket (``POST /v2/auth/socket-tickets``) and
opens the socket with ``?ticket=`` (``contracts/schemas/websocket/README.md``).

The ticket is a PyJWT HS256 token (``sub`` = the caller, a dedicated ``aud``,
``exp`` = ``MORPHLOOP_SOCKET_TICKET_TTL_SECONDS``, a random ``jti``) signed
with ``MORPHLOOP_SOCKET_TICKET_SECRET``: stateless to verify, no DB round trip
on issue. Single use holds across API processes: redeeming takes the claims
lease ``socket-ticket:<jti>`` (``ClaimStore``, #192) until just after the
ticket expires, so a second redemption on any worker is refused.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from starlette.requests import HTTPConnection

from harness.core.ports.auth import AuthError
from harness.core.ports.claims import ClaimStore
from harness.core.settings import Settings

AUDIENCE = "morphloop:socket-ticket"
# The lease outlives the ticket by this much, so a store clock a little behind
# this process's clock never frees a jti the ticket is still valid for.
CLOCK_SKEW = timedelta(seconds=30)
_build_lock = threading.Lock()


class SocketTickets:
    """Issues and redeems tickets for one secret and TTL."""

    def __init__(self, *, secret: str, ttl_seconds: int) -> None:
        self.secret = secret
        self.ttl_seconds = ttl_seconds

    def issue(self, user_id: str) -> tuple[str, datetime]:
        now = int(time.time())
        exp = now + self.ttl_seconds
        claims = {
            "sub": user_id,
            "aud": AUDIENCE,
            "iat": now,
            "exp": exp,
            "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256"), datetime.fromtimestamp(exp, UTC)

    def redeem(self, ticket: str, claims_store: ClaimStore) -> str:
        """The ticket's user id; `AuthError` if it is invalid, expired or already
        used (on any process sharing ``claims_store``)."""
        try:
            claims = jwt.decode(
                ticket,
                self.secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                options={"require": ["sub", "aud", "exp", "jti"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid socket ticket: {exc}") from exc
        remaining = timedelta(seconds=max(float(claims["exp"]) - time.time(), 0.0))
        # A fresh holder: the same holder would renew its own lease and win again.
        if not claims_store.try_claim(
            f"socket-ticket:{claims['jti']}", uuid.uuid4().hex, remaining + CLOCK_SKEW
        ):
            raise AuthError("socket ticket already used")
        return str(claims["sub"])


def socket_tickets_from_settings() -> SocketTickets:
    settings = Settings()
    secret = settings.morphloop_socket_ticket_secret
    if secret is None:
        if settings.morphloop_environment == "production":
            raise RuntimeError("MORPHLOOP_SOCKET_TICKET_SECRET is required in production")
        # Local development: a per-process secret (tickets die with the process).
        secret = secrets.token_urlsafe(32)
    return SocketTickets(secret=secret, ttl_seconds=settings.morphloop_socket_ticket_ttl_seconds)


def socket_tickets_of(conn: HTTPConnection) -> SocketTickets:
    """The app's tickets; built from settings and cached on ``app.state`` on first use."""
    # Locked: two racing builds would hold different dev secrets.
    with _build_lock:
        tickets: SocketTickets | None = getattr(conn.app.state, "socket_tickets", None)
        if tickets is None:
            tickets = socket_tickets_from_settings()
            conn.app.state.socket_tickets = tickets
        return tickets
