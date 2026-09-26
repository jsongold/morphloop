"""``dev``: every request is the fixed ``MORPHLOOP_USER_ID``; refused in production."""

from __future__ import annotations

from harness.core.settings import Settings


class DevAuthProvider:
    """No verification at all -- local development and tests only."""

    def __init__(self) -> None:
        if Settings().morphloop_environment == "production":
            raise RuntimeError(
                "the dev auth provider is refused when MORPHLOOP_ENVIRONMENT=production"
            )

    def user_id(self, token: str | None) -> str:
        # Read per call, like every Settings use, so tests can monkeypatch it.
        return Settings().morphloop_user_id
