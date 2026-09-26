"""Choosing the ``AuthProvider`` (#171, #186): the SDK offers, the app chooses.

``create_app(auth=...)`` stores the app's provider on ``app.state.auth_provider``
and it wins. Otherwise the provider is built from ``MORPHLOOP_AUTH_PROVIDER``
(``dev`` by default) at app startup -- so production with ``dev``, or with an
incomplete oidc/supabase config, refuses to start -- and cached there.
"""

from __future__ import annotations

from starlette.requests import HTTPConnection

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
        issuer = settings.morphloop_oidc_issuer
        audience = settings.morphloop_oidc_audience
        jwks_url = settings.morphloop_oidc_jwks_url
        if not (issuer and audience and jwks_url):
            raise ValueError(
                "MORPHLOOP_AUTH_PROVIDER=oidc needs MORPHLOOP_OIDC_ISSUER, "
                "MORPHLOOP_OIDC_AUDIENCE and MORPHLOOP_OIDC_JWKS_URL"
            )
        return OidcAuthProvider(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            algorithms=settings.oidc_algorithms,
            leeway=settings.morphloop_oidc_leeway,
        )
    return DevAuthProvider()


def auth_provider_of(request: HTTPConnection) -> AuthProvider:
    provider: AuthProvider | None = getattr(request.app.state, "auth_provider", None)
    if provider is None:
        provider = auth_provider_from_settings()
        request.app.state.auth_provider = provider
    return provider
