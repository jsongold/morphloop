"""Choosing the ``AuthProvider`` (#171, #186): the SDK offers, the app chooses.

``create_app(auth=...)`` stores the app's provider on ``app.state.auth_provider``
and it wins. Otherwise the provider is built once from
``MORPHLOOP_AUTH_PROVIDER`` (``dev`` by default; ``Settings`` refuses ``dev``
in production) and cached there on first use.
"""

from __future__ import annotations

from fastapi import Request

from harness.adapters.auth import DevAuthProvider, OidcAuthProvider
from harness.adapters.supabase import supabase_auth
from harness.core.ports.auth import AuthProvider
from harness.core.settings import Settings


def auth_provider_from_settings() -> AuthProvider:
    settings = Settings()
    if settings.morphloop_auth_provider == "supabase":
        return supabase_auth(
            project_ref=settings.morphloop_supabase_project_ref,
            url=settings.morphloop_supabase_url,
            algorithms=settings.oidc_algorithms,
            leeway=settings.morphloop_oidc_leeway,
        )
    if settings.morphloop_auth_provider == "oidc":
        if not settings.morphloop_oidc_jwks_url:
            raise RuntimeError("MORPHLOOP_AUTH_PROVIDER=oidc needs MORPHLOOP_OIDC_JWKS_URL")
        return OidcAuthProvider(
            issuer=settings.morphloop_oidc_issuer or "",
            audience=settings.morphloop_oidc_audience or "",
            jwks_url=settings.morphloop_oidc_jwks_url,
            algorithms=settings.oidc_algorithms,
            leeway=settings.morphloop_oidc_leeway,
        )
    return DevAuthProvider()


def auth_provider_of(request: Request) -> AuthProvider:
    provider: AuthProvider | None = getattr(request.app.state, "auth_provider", None)
    if provider is None:
        provider = auth_provider_from_settings()
        request.app.state.auth_provider = provider
    return provider
