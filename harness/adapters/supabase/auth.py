"""``supabase``: OIDC verification with Supabase Auth's issuer, audience and JWKS."""

from __future__ import annotations

from collections.abc import Sequence

import jwt

from harness.adapters.auth.oidc import OidcAuthProvider


def supabase_auth(
    *,
    project_ref: str | None = None,
    url: str | None = None,
    algorithms: Sequence[str] = ("RS256", "ES256"),
    leeway: int = 60,
    jwks_client: jwt.PyJWKClient | None = None,
) -> OidcAuthProvider:
    """``url`` (e.g. a custom domain) wins over ``https://<project_ref>.supabase.co``."""
    base = url.rstrip("/") if url else f"https://{project_ref}.supabase.co" if project_ref else None
    if base is None:
        raise ValueError("supabase auth needs a project_ref or a url")
    return OidcAuthProvider(
        issuer=f"{base}/auth/v1",
        audience="authenticated",
        jwks_url=f"{base}/auth/v1/.well-known/jwks.json",
        algorithms=algorithms,
        leeway=leeway,
        jwks_client=jwks_client,
    )
