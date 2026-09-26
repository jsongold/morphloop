"""``oidc``: verify a bearer JWT against an OIDC provider's JWKS (PyJWT).

Hardening, each a line below: algorithms are pinned by the operator, never
taken from the token header (so ``none`` and HS256-with-a-public-key are
refused); only asymmetric algorithms are accepted; ``exp`` / ``iat`` / ``sub``
are required; issuer and audience are always checked; leeway is at most 60 s;
the JWKS URL must be https. An unknown ``kid`` refetches the JWKS once
(PyJWKClient, rate-limited by its cooldown) to follow key rotation.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import jwt

from harness.core.ports.auth import AuthError, AuthUnavailableError, user_id_for

ASYMMETRIC_PREFIXES = ("RS", "PS", "ES", "Ed")


class OidcAuthProvider:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        leeway: int = 60,
        jwks_client: jwt.PyJWKClient | None = None,
    ) -> None:
        if not issuer or not audience:
            raise ValueError("oidc auth needs both an issuer and an audience")
        if not jwks_url.startswith("https://"):
            raise ValueError(f"the JWKS URL must be https://, got {jwks_url!r}")
        if not algorithms or not all(a.startswith(ASYMMETRIC_PREFIXES) for a in algorithms):
            raise ValueError(
                f"oidc auth accepts asymmetric algorithms only, got {list(algorithms)}"
            )
        if not 0 <= leeway <= 60:
            raise ValueError(f"leeway must be 0..60 seconds, got {leeway}")
        self.issuer = issuer
        self.audience = audience
        self.algorithms = list(algorithms)
        self.leeway = leeway
        # ponytail: a 5 s refetch cooldown lets a rotated key in promptly while
        # capping unknown-kid refetches at one per 5 s; make it a knob if an IdP needs less.
        self.jwks_client = jwks_client or jwt.PyJWKClient(jwks_url, timeout=5, cooldown_duration=5)

    def user_id(self, token: str | None) -> str:
        if not token:
            raise AuthError("a bearer token is required")
        try:
            key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except (jwt.PyJWKClientConnectionError, jwt.PyJWKSetError, json.JSONDecodeError) as exc:
            raise AuthUnavailableError(f"could not use the JWKS: {exc}") from exc
        except jwt.PyJWKClientError as exc:
            # PyJWT has one error class for both; only an unknown kid is the token's fault.
            if str(exc).startswith("Unable to find a signing key"):
                raise AuthError(str(exc)) from exc
            raise AuthUnavailableError(f"could not use the JWKS: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc
        if not claims["sub"]:
            raise AuthError("the token's sub claim is empty")
        return user_id_for(self.issuer, claims["sub"])
