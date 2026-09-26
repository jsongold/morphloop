"""Environment variables the harness reads directly (pydantic-settings).

One :class:`Settings` model for the env vars the harness reads:
``MORPHLOOP_CONTRACTS_DIR``, ``DATABASE_URL``, ``WEB_ORIGIN``, ``WEB_ORIGINS``,
``HARNESS_VERSION``, ``MORPHLOOP_PACK_V2_DIR``, ``MORPHLOOP_USER_ID``,
``MORPHLOOP_LLM_PROVIDER``, ``MORPHLOOP_ENVIRONMENT``, ``MORPHLOOP_EMBEDDING_DIMS``, the
``MORPHLOOP_AUTH_PROVIDER`` / ``MORPHLOOP_DB_PROVIDER`` / ``MORPHLOOP_OIDC_*``
/ ``MORPHLOOP_SUPABASE_*`` / ``MORPHLOOP_SOCKET_TICKET_*`` / ``MORPHLOOP_USER_*``
auth-platform settings (#169; this issue is settings only -- the provider
implementations are #171 and #186).
Field names match their env var names case-insensitively (the library
default), so no prefix or alias mapping is needed.

Callers must instantiate ``Settings()`` at the point of use, not cache it at
import time: pydantic-settings reads the environment when the model is
constructed, and tests (``tests/api/test_health.py``,
``tests/adapters/postgres/conftest.py``) monkeypatch these env vars per test,
expecting the next read to see the new value.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings

# Symmetric algorithms need a shared secret, not a public JWKS endpoint --
# incompatible with the JWKS-based verification these settings configure.
_SYMMETRIC_ALGORITHMS = {"HS256", "HS384", "HS512"}


class Settings(BaseSettings):
    """Env vars read directly by the harness (names and defaults unchanged)."""

    morphloop_contracts_dir: str | None = None
    database_url: str = "postgresql+psycopg://morphloop:morphloop@localhost:5432/morphloop"
    # v0.4 (#169): an optional direct (non-pooled) connection for migrations,
    # for providers whose DATABASE_URL is a pooler (e.g. Supabase's Supavisor
    # on port 6543 vs the direct Postgres port 5432). Unset: migrations use
    # DATABASE_URL as-is. Supavisor transaction-mode pooling additionally
    # needs psycopg `prepare_threshold=None`; that wiring is a separate issue.
    morphloop_database_direct_url: str | None = None
    web_origin: str = "http://localhost:3000"
    # v0.4 (#169): comma-separated extra CORS origins, for a second front-end
    # origin. Unset falls back to ``[web_origin]`` alone; WEB_ORIGIN keeps working.
    web_origins: str | None = None
    harness_version: str | None = None
    # v0.2.0 (#77): the v2 pack directory (no default: the harness names no pack)
    # and the single learner's id (`contracts/schemas/common/ids.json` user_id).
    morphloop_pack_v2_dir: str | None = None
    morphloop_user_id: str = Field(default="usr_local", pattern=r"^usr_[0-9A-Za-z]{1,64}$")
    # #130: "fake" swaps in a deterministic, keyless LLM for dev-stack / local E2E
    # (harness.cli.wiring.llm_provider); refused when morphloop_environment is
    # "production".
    morphloop_llm_provider: str = "litellm"
    morphloop_environment: str = "development"

    # v0.4 (#169): provider selectors for the auth platform (#152), the same
    # shape as MORPHLOOP_LLM_PROVIDER. "dev" is today's fixed MORPHLOOP_USER_ID
    # and is refused in production (see the validator below). Literal types
    # (not a production-only check) so a typo is rejected in every environment.
    morphloop_auth_provider: Literal["dev", "oidc", "supabase"] = "dev"
    morphloop_db_provider: Literal["postgres", "supabase"] = "postgres"

    # Generic OIDC fields (auth_provider="oidc"). No default issuer/audience:
    # production must set them. Example for a self-hosted OIDC provider:
    # issuer "https://idp.example.com", jwks_url
    # "https://idp.example.com/.well-known/jwks.json".
    morphloop_oidc_issuer: str | None = None
    morphloop_oidc_audience: str | None = None
    morphloop_oidc_jwks_url: str | None = None
    # Supabase may sign with either RS256 or ES256, so both are allowed by
    # default; a self-hosted OIDC provider that only issues one can narrow this.
    morphloop_oidc_algorithms: str = "RS256,ES256"
    morphloop_oidc_leeway: int = Field(default=60, ge=0, le=60)

    # Supabase preset (auth_provider="supabase"): only project_ref or url is
    # needed, and issuer/audience/jwks_url are derived (see the properties
    # below) as: jwks "https://<ref>.supabase.co/auth/v1/.well-known/jwks.json",
    # iss "https://<ref>.supabase.co/auth/v1", aud "authenticated". Supabase
    # signs with asymmetric keys only (ES256/RS256); legacy HS256 is unsupported.
    morphloop_supabase_project_ref: str | None = None
    morphloop_supabase_url: str | None = None

    # v0.4 (#169): the short-lived ticket a WS handshake trades for a session
    # (browsers can't set an Authorization header on a WS upgrade).
    morphloop_socket_ticket_secret: str | None = None
    morphloop_socket_ticket_ttl_seconds: int = Field(default=60, gt=0)

    # v0.4 (#169): per-user limits (#152 "Per-user lab / LLM call limits").
    morphloop_user_max_concurrent_labs: int = Field(default=1, gt=0)
    morphloop_user_llm_calls_per_minute: int = Field(default=20, gt=0)

    # v0.4 (#180): the pgvector column size, fixed when the search migration
    # runs. <=1536 keeps a plain ``vector`` HNSW-indexable (the cap is 2000);
    # it must equal the pack's declared embedding dims.
    morphloop_embedding_dims: int = Field(default=1536, gt=0, le=1536)

    @property
    def cors_origins(self) -> list[str]:
        """The CORS ``allow_origins`` list: ``web_origin`` plus ``web_origins``'
        comma-separated extras (deduplicated, order preserved)."""
        extra = self.web_origins.split(",") if self.web_origins else []
        origins = [self.web_origin, *(origin.strip() for origin in extra)]
        seen: set[str] = set()
        deduped = []
        for origin in origins:
            if origin and origin not in seen:
                seen.add(origin)
                deduped.append(origin)
        return deduped

    @property
    def oidc_algorithms(self) -> list[str]:
        """``morphloop_oidc_algorithms`` split on commas (PyJWT's ``algorithms=``)."""
        return [alg.strip() for alg in self.morphloop_oidc_algorithms.split(",") if alg.strip()]

    def _supabase_base_url(self) -> str | None:
        if self.morphloop_supabase_url is not None:
            return self.morphloop_supabase_url.rstrip("/")
        if self.morphloop_supabase_project_ref is not None:
            return f"https://{self.morphloop_supabase_project_ref}.supabase.co"
        return None

    @property
    def effective_oidc_issuer(self) -> str | None:
        """``morphloop_oidc_issuer``, or the Supabase preset's issuer when
        ``morphloop_auth_provider`` is "supabase"."""
        if self.morphloop_auth_provider == "supabase":
            base = self._supabase_base_url()
            return f"{base}/auth/v1" if base else None
        return self.morphloop_oidc_issuer

    @property
    def effective_oidc_audience(self) -> str | None:
        """``morphloop_oidc_audience``, or "authenticated" under the Supabase preset."""
        if self.morphloop_auth_provider == "supabase":
            return "authenticated"
        return self.morphloop_oidc_audience

    @property
    def effective_oidc_jwks_url(self) -> str | None:
        """``morphloop_oidc_jwks_url``, or the Supabase preset's JWKS URL."""
        if self.morphloop_auth_provider == "supabase":
            base = self._supabase_base_url()
            return f"{base}/auth/v1/.well-known/jwks.json" if base else None
        return self.morphloop_oidc_jwks_url

    @field_validator("morphloop_oidc_jwks_url", "morphloop_supabase_url")
    @classmethod
    def _jwks_url_is_https(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError(f"{info.field_name} must be an https:// URL")
        return value

    @field_validator("morphloop_oidc_algorithms")
    @classmethod
    def _algorithms_are_asymmetric(cls, value: str) -> str:
        algorithms = {alg.strip() for alg in value.split(",") if alg.strip()}
        symmetric = algorithms & _SYMMETRIC_ALGORITHMS
        if symmetric:
            raise ValueError(
                f"morphloop_oidc_algorithms rejects symmetric algorithms {sorted(symmetric)}: "
                "JWKS-based verification needs an asymmetric algorithm (e.g. RS256, ES256)"
            )
        return value

    @model_validator(mode="after")
    def _production_requires_auth(self) -> Settings:
        if self.morphloop_environment != "production":
            return self
        if self.morphloop_auth_provider == "dev":
            raise ValueError(
                "MORPHLOOP_ENVIRONMENT=production refuses MORPHLOOP_AUTH_PROVIDER=dev "
                "(the fixed local user id)"
            )
        if self.morphloop_auth_provider == "oidc" and (
            self.effective_oidc_issuer is None or self.effective_oidc_audience is None
        ):
            raise ValueError(
                "MORPHLOOP_ENVIRONMENT=production with MORPHLOOP_AUTH_PROVIDER=oidc requires "
                "MORPHLOOP_OIDC_ISSUER and MORPHLOOP_OIDC_AUDIENCE"
            )
        if self.morphloop_auth_provider == "supabase" and self._supabase_base_url() is None:
            raise ValueError(
                "MORPHLOOP_ENVIRONMENT=production with MORPHLOOP_AUTH_PROVIDER=supabase "
                "requires MORPHLOOP_SUPABASE_PROJECT_REF or MORPHLOOP_SUPABASE_URL"
            )
        return self
