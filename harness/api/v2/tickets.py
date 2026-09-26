"""Short-lived, single-use WebSocket tickets (#172).

A browser cannot set ``Authorization`` on a WebSocket upgrade, so the client
trades its bearer token for a ticket (``POST /v2/auth/socket-tickets``) and
opens the socket with ``?ticket=`` (``contracts/schemas/websocket/README.md``).

The ticket is a PyJWT HS256 token (``sub`` = the caller, a dedicated ``aud``,
``exp`` = ``MORPHLOOP_SOCKET_TICKET_TTL_SECONDS``, a random ``jti``) signed
with ``MORPHLOOP_SOCKET_TICKET_SECRET``: stateless to verify, no DB round trip
on issue. Single use is a per-process set of redeemed ``jti`` values, pruned
as they expire.
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import UTC, datetime

import jwt
from starlette.requests import HTTPConnection

from harness.core.ports.auth import AuthError
from harness.core.settings import Settings

AUDIENCE = "morphloop:socket-ticket"
_build_lock = threading.Lock()


class SocketTickets:
    """Issues and redeems tickets for one secret and TTL."""

    def __init__(self, *, secret: str, ttl_seconds: int) -> None:
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        # ponytail: redeemed jti set is per process; with several workers a
        # ticket could be replayed once per worker within its TTL. Move it to
        # a `claims` row keyed by jti (#174) when running multi-worker.
        self.redeemed: dict[str, float] = {}
        self.lock = threading.Lock()

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

    def redeem(self, ticket: str) -> str:
        """The ticket's user id; `AuthError` if it is invalid, expired or already used."""
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
        now = time.time()
        with self.lock:
            self.redeemed = {jti: exp for jti, exp in self.redeemed.items() if exp > now}
            if claims["jti"] in self.redeemed:
                raise AuthError("socket ticket already used")
            self.redeemed[claims["jti"]] = float(claims["exp"])
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
    # Locked: two racing builds would hold different dev secrets / redeemed sets.
    with _build_lock:
        tickets: SocketTickets | None = getattr(conn.app.state, "socket_tickets", None)
        if tickets is None:
            tickets = socket_tickets_from_settings()
            conn.app.state.socket_tickets = tickets
        return tickets
