"""Tests for :class:`harness.core.settings.Settings` (#169)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.api.v2.auth import auth_provider_from_settings
from harness.core.settings import Settings


def test_cors_origins_falls_back_to_web_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_ORIGINS", raising=False)
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:3000")

    assert Settings().cors_origins == ["http://localhost:3000"]


def test_web_origins_extends_web_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ORIGIN", "https://old.example.com")
    monkeypatch.setenv("WEB_ORIGINS", "https://a.example.com, https://b.example.com")

    assert Settings().cors_origins == [
        "https://old.example.com",
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_web_origins_deduplicates_against_web_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ORIGIN", "https://a.example.com")
    monkeypatch.setenv("WEB_ORIGINS", "https://a.example.com, https://b.example.com")

    assert Settings().cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_oidc_jwks_url_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_OIDC_JWKS_URL", "http://issuer.example.com/jwks.json")

    with pytest.raises(ValidationError):
        Settings()


def test_oidc_jwks_url_accepts_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_OIDC_JWKS_URL", "https://issuer.example.com/jwks.json")

    assert Settings().morphloop_oidc_jwks_url == "https://issuer.example.com/jwks.json"


def test_oidc_algorithms_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_OIDC_ALGORITHMS", "RS256, ES256")

    assert Settings().oidc_algorithms == ["RS256", "ES256"]


def test_oidc_algorithms_default_allows_rs256_and_es256(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_OIDC_ALGORITHMS", raising=False)

    assert Settings().oidc_algorithms == ["RS256", "ES256"]


def test_oidc_algorithms_rejects_symmetric_hs256(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_OIDC_ALGORITHMS", "HS256")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("leeway", [-1, 61])
def test_oidc_leeway_out_of_range_is_rejected(monkeypatch: pytest.MonkeyPatch, leeway: int) -> None:
    monkeypatch.setenv("MORPHLOOP_OIDC_LEEWAY", str(leeway))

    with pytest.raises(ValidationError):
        Settings()


def test_unknown_auth_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "deev")  # typo

    with pytest.raises(ValidationError):
        Settings()


def test_supabase_url_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_SUPABASE_URL", "http://abcdefgh.supabase.co")

    with pytest.raises(ValidationError):
        Settings()


def test_production_refuses_dev_auth_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings still load (an app may pass create_app(auth=...)); the dev
    # provider itself refuses to be built (#171).
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.delenv("MORPHLOOP_AUTH_PROVIDER", raising=False)
    assert Settings().morphloop_auth_provider == "dev"
    with pytest.raises(RuntimeError):
        auth_provider_from_settings()


def test_production_oidc_requires_issuer_and_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "oidc")
    monkeypatch.delenv("MORPHLOOP_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("MORPHLOOP_OIDC_AUDIENCE", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_production_starts_with_oidc_issuer_and_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("MORPHLOOP_OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("MORPHLOOP_OIDC_AUDIENCE", "morphloop-api")

    settings = Settings()

    assert settings.effective_oidc_issuer == "https://issuer.example.com"
    assert settings.effective_oidc_audience == "morphloop-api"


def test_production_supabase_requires_project_ref_or_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "supabase")
    monkeypatch.delenv("MORPHLOOP_SUPABASE_PROJECT_REF", raising=False)
    monkeypatch.delenv("MORPHLOOP_SUPABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_supabase_preset_derives_issuer_audience_and_jwks_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("MORPHLOOP_SUPABASE_PROJECT_REF", "abcdefgh")

    settings = Settings()

    assert settings.effective_oidc_issuer == "https://abcdefgh.supabase.co/auth/v1"
    assert settings.effective_oidc_audience == "authenticated"
    assert (
        settings.effective_oidc_jwks_url
        == "https://abcdefgh.supabase.co/auth/v1/.well-known/jwks.json"
    )


def test_development_defaults_to_dev_auth_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "development")
    monkeypatch.delenv("MORPHLOOP_AUTH_PROVIDER", raising=False)

    settings = Settings()  # does not raise

    assert settings.morphloop_auth_provider == "dev"
