"""OIDC / Supabase auth providers against a local test JWKS (#171, #186)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from harness.adapters.auth import DevAuthProvider, OidcAuthProvider
from harness.adapters.supabase import supabase_auth
from harness.core.ports.auth import AuthError, AuthUnavailableError, user_id_for

ISS = "https://idp.example.com"
AUD = "morphloop"


class FakeJwks(jwt.PyJWKClient):
    """PyJWKClient serving ``keys`` from memory; ``down`` simulates an outage."""

    def __init__(self, *keys: Any) -> None:
        super().__init__("https://idp.example.com/jwks.json", cooldown_duration=0)
        self.keys = list(keys)
        self.down = False
        self.fetches = 0

    def fetch_data(self) -> Any:
        self.fetches += 1
        if self.down:
            raise jwt.PyJWKClientConnectionError("connection refused")
        return {"keys": [jwk(key, kid) for kid, key in self.keys]}


def jwk(private_key: Any, kid: str) -> dict[str, Any]:
    algo = (
        jwt.algorithms.RSAAlgorithm
        if isinstance(private_key, rsa.RSAPrivateKey)
        else jwt.algorithms.ECAlgorithm
    )
    data: dict[str, Any] = json.loads(algo.to_jwk(private_key.public_key()))
    return {**data, "kid": kid, "use": "sig"}


RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC = ec.generate_private_key(ec.SECP256R1())


def token(key: Any = RSA, *, kid: str = "k1", alg: str = "RS256", **claims: Any) -> str:
    now = int(time.time())
    body = {"iss": ISS, "aud": AUD, "sub": "alice", "iat": now, "exp": now + 300, **claims}
    return jwt.encode(
        {k: v for k, v in body.items() if v is not None}, key, algorithm=alg, headers={"kid": kid}
    )


def provider(client: FakeJwks | None = None, **kwargs: Any) -> OidcAuthProvider:
    return OidcAuthProvider(
        issuer=ISS,
        audience=AUD,
        jwks_url="https://idp.example.com/jwks.json",
        jwks_client=client or FakeJwks(("k1", RSA)),
        **kwargs,
    )


def test_valid_token_resolves_to_uuid5_user_id() -> None:
    user_id = provider().user_id(token())
    assert user_id == user_id_for(ISS, "alice")
    assert user_id.startswith("usr_") and len(user_id) == 36
    assert user_id != user_id_for("https://other.example.com", "alice")


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 120},  # past the 60 s leeway
        {"exp": None},
        {"iat": None},
        {"sub": None},
        {"sub": ""},
    ],
)
def test_bad_claims_are_unauthorized(claims: dict[str, Any]) -> None:
    with pytest.raises(AuthError):
        provider().user_id(token(**claims))


def test_missing_token_is_unauthorized() -> None:
    with pytest.raises(AuthError):
        provider().user_id(None)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_hs256_signed_with_the_public_key_is_refused() -> None:
    """The classic alg confusion: an HMAC over the token keyed by the public PEM."""
    public_pem = RSA.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    claims = {"iss": ISS, "aud": AUD, "sub": "alice", "iat": now, "exp": now + 300}
    signing_input = f"{header}.{b64(json.dumps(claims).encode())}"
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    with pytest.raises(AuthError):
        provider().user_id(f"{signing_input}.{b64(signature)}")


def test_alg_none_is_refused() -> None:
    now = int(time.time())
    unsigned = jwt.encode(
        {"iss": ISS, "aud": AUD, "sub": "alice", "iat": now, "exp": now + 300},
        None,
        algorithm="none",
        headers={"kid": "k1"},
    )
    with pytest.raises(AuthError):
        provider().user_id(unsigned)


def test_unknown_kid_refetches_the_jwks_once() -> None:
    client = FakeJwks(("k1", RSA))
    auth = provider(client)
    auth.user_id(token())
    client.keys.append(("k2", EC))  # the IdP rotates in a new key
    assert auth.user_id(token(EC, kid="k2", alg="ES256")) == user_id_for(ISS, "alice")
    assert client.fetches == 2
    with pytest.raises(AuthError):
        auth.user_id(token(kid="nope"))


def test_jwks_outage_is_unavailable() -> None:
    client = FakeJwks(("k1", RSA))
    client.down = True
    with pytest.raises(AuthUnavailableError):
        provider(client).user_id(token())


def test_real_client_outage_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", refuse)
    auth = OidcAuthProvider(issuer=ISS, audience=AUD, jwks_url="https://idp.example.com/jwks.json")
    with pytest.raises(AuthUnavailableError):
        auth.user_id(token())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"jwks_url": "http://idp.example.com/jwks.json"},
        {"algorithms": ["HS256"]},
        {"algorithms": ["none"]},
        {"leeway": 61},
        {"issuer": ""},
        {"audience": ""},
    ],
)
def test_unsafe_configuration_is_refused(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {"issuer": ISS, "audience": AUD, "jwks_url": "https://idp.example.com/j"}
    with pytest.raises(ValueError):
        OidcAuthProvider(**{**args, **kwargs})


def test_supabase_preset_verifies_a_supabase_token() -> None:
    auth = supabase_auth(project_ref="abcd", jwks_client=FakeJwks(("sb1", EC)))
    assert auth.issuer == "https://abcd.supabase.co/auth/v1"
    assert auth.audience == "authenticated"
    assert supabase_auth(project_ref="abcd").jwks_client.uri == (
        "https://abcd.supabase.co/auth/v1/.well-known/jwks.json"
    )
    now = int(time.time())
    supabase_token = jwt.encode(
        {
            "iss": "https://abcd.supabase.co/auth/v1",
            "aud": "authenticated",
            "sub": "5f0c1c1e-0000-4000-8000-000000000001",
            "role": "authenticated",
            "email": "a@example.com",
            "session_id": "s1",
            "iat": now,
            "exp": now + 3600,
        },
        EC,
        algorithm="ES256",
        headers={"kid": "sb1", "typ": "JWT"},
    )
    assert auth.user_id(supabase_token) == user_id_for(
        "https://abcd.supabase.co/auth/v1", "5f0c1c1e-0000-4000-8000-000000000001"
    )


def test_supabase_refuses_hs256() -> None:
    with pytest.raises(ValueError):
        supabase_auth(project_ref="abcd", algorithms=["HS256"])


def test_dev_provider_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DevAuthProvider().user_id(None) == "usr_local"
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    monkeypatch.setenv("MORPHLOOP_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("MORPHLOOP_OIDC_ISSUER", ISS)
    monkeypatch.setenv("MORPHLOOP_OIDC_AUDIENCE", AUD)
    with pytest.raises(RuntimeError):
        DevAuthProvider()


@pytest.mark.parametrize("jwks", [{"keys": []}, {"keys": [{"kty": "RSA", "use": "enc"}]}, []])
def test_unusable_jwks_is_unavailable(jwks: Any) -> None:
    client = FakeJwks()
    client.fetch_data = lambda: jwks  # type: ignore[method-assign]
    with pytest.raises(AuthUnavailableError):
        provider(client).user_id(token())


def test_real_client_refetches_a_rotated_key_promptly() -> None:
    auth = OidcAuthProvider(issuer=ISS, audience=AUD, jwks_url="https://idp.example.com/j")
    assert auth.jwks_client.cooldown_duration <= 5
