"""UserIdDep through the app's AuthProvider: create_app(auth=...) wins over
MORPHLOOP_AUTH_PROVIDER; errors are 401 / 503 problems (#171, #186)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from harness.adapters.auth import DevAuthProvider, OidcAuthProvider
from harness.api.app import check_db, create_app
from harness.api.problems import install_handlers
from harness.api.v2.auth import auth_provider_from_settings
from harness.api.v2.deps import UserIdDep, event_store_v2_of
from harness.core.ports.auth import AuthError, AuthUnavailableError


class StubAuth:
    def user_id(self, token: str | None) -> str:
        if token == "down":
            raise AuthUnavailableError("jwks unreachable")
        if token != "good":
            raise AuthError("bad token")
        return "usr_stub"


def probe_app(app: FastAPI) -> TestClient:
    @app.get("/whoami")
    def whoami(user_id: UserIdDep) -> dict[str, str]:
        return {"user_id": user_id}

    return TestClient(app)


def test_create_app_auth_takes_precedence() -> None:
    client = probe_app(create_app(auth=StubAuth()))
    ok = client.get("/whoami", headers={"Authorization": "Bearer good"})
    assert ok.json() == {"user_id": "usr_stub"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer bad"}, {"Authorization": "x"}])
def test_invalid_or_missing_token_is_401_problem(headers: dict[str, str]) -> None:
    response = probe_app(create_app(auth=StubAuth())).get("/whoami", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_idp_outage_is_503_problem() -> None:
    client = probe_app(create_app(auth=StubAuth()))
    response = client.get("/whoami", headers={"Authorization": "Bearer down"})
    assert response.status_code == 503
    assert response.json()["code"] == "auth-unavailable"


def test_403_http_exception_is_forbidden_problem() -> None:
    app = FastAPI()
    install_handlers(app)

    @app.get("/nope")
    def nope() -> None:
        raise HTTPException(403, "no permission")

    response = TestClient(app).get("/nope")
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")


def test_default_is_dev_fixed_user() -> None:
    client = probe_app(create_app())
    assert client.get("/whoami").json() == {"user_id": "usr_local"}
    assert isinstance(auth_provider_from_settings(), DevAuthProvider)


def test_settings_select_oidc_and_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("MORPHLOOP_OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("MORPHLOOP_OIDC_AUDIENCE", "morphloop")
    monkeypatch.setenv("MORPHLOOP_OIDC_JWKS_URL", "https://idp.example.com/jwks.json")
    monkeypatch.setenv("MORPHLOOP_OIDC_ALGORITHMS", "ES256")
    oidc = auth_provider_from_settings()
    assert isinstance(oidc, OidcAuthProvider)
    assert (oidc.issuer, oidc.audience, oidc.algorithms) == (
        "https://idp.example.com",
        "morphloop",
        ["ES256"],
    )
    # A request without a token is refused, not served as the dev user.
    assert probe_app(create_app()).get("/whoami").status_code == 401

    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("MORPHLOOP_SUPABASE_PROJECT_REF", "abcd")
    supabase = auth_provider_from_settings()
    assert isinstance(supabase, OidcAuthProvider)
    assert supabase.issuer == "https://abcd.supabase.co/auth/v1"
    assert supabase.audience == "authenticated"


def test_production_app_with_explicit_provider_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    client = probe_app(create_app(auth=StubAuth()))
    assert client.get("/whoami", headers={"Authorization": "Bearer good"}).status_code == 200


def test_v2_authenticates_before_opening_the_database() -> None:
    app = create_app(auth=StubAuth())

    def no_db() -> None:
        raise AssertionError("the store must not be built for an anonymous request")

    app.dependency_overrides[event_store_v2_of] = no_db
    response = TestClient(app).get("/v2/ws")
    assert response.status_code == 401


def test_production_with_dev_provider_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError), TestClient(create_app()):
        pass


def test_explicit_auth_ignores_unused_provider_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "oidc")  # incomplete, and unused
    app = create_app(auth=StubAuth())
    app.dependency_overrides[check_db] = lambda: True
    with probe_app(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/whoami", headers={"Authorization": "Bearer good"}).status_code == 200


def test_local_dev_needs_no_token() -> None:
    """An app built on the SDK (e.g. browncircle) running locally with defaults."""
    with probe_app(create_app()) as client:
        assert client.get("/whoami").json() == {"user_id": "usr_local"}
