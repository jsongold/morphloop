"""The ``AuthProvider`` Port: who is calling (#171, #186).

The SDK offers providers (``harness.adapters.auth``: ``dev`` / ``oidc``;
``harness.adapters.supabase``: a preset over ``oidc``) and the app chooses
one: ``create_app(auth=...)``, else ``MORPHLOOP_AUTH_PROVIDER``. Core only
knows this Port.

A provider turns the request's bearer token (``None`` when absent) into the
internal ``usr_…`` id every route scopes by. It raises
:class:`AuthError` (401 ``unauthorized``) for a missing or invalid token and
:class:`AuthUnavailableError` (503 ``auth-unavailable``) when the identity
provider cannot be reached to verify it. ``user_id`` is synchronous and may
block on network I/O; FastAPI runs it in the thread pool.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class AuthError(Exception):
    """The bearer token is missing, malformed, expired or not trusted (401)."""


class AuthUnavailableError(Exception):
    """The identity provider could not be reached to verify a token (503)."""


class AuthProvider(Protocol):
    def user_id(self, token: str | None) -> str:
        """The caller's internal ``usr_…`` id for ``token``."""
        ...


def user_id_for(issuer: str, subject: str) -> str:
    """The stable internal id of a verified ``(iss, sub)``: ``usr_<uuid5 hex>``.

    Scoped by issuer, so the same ``sub`` from two identity providers is two
    learners, and no raw external id is stored in events.
    """
    return f"usr_{uuid.uuid5(uuid.NAMESPACE_URL, f'{issuer}|{subject}').hex}"
