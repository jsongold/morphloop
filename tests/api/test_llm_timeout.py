"""Startup refuses an LLM timeout that leaves no margin under the judge lease (#203)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness.api.app import check_llm_timeout_under_lease, create_app
from harness.core.drill.judge import JUDGE_LEASE_TTL
from harness.core.settings import Settings


def test_default_settings_pass_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_LLM_TIMEOUT_SECONDS", raising=False)

    check_llm_timeout_under_lease()  # does not raise


def test_timeout_equal_to_the_lease_is_rejected() -> None:
    settings = Settings(morphloop_llm_timeout_seconds=JUDGE_LEASE_TTL.total_seconds())

    with pytest.raises(ValueError, match="judge lease TTL"):
        check_llm_timeout_under_lease(settings)


def test_timeout_over_the_lease_is_rejected() -> None:
    settings = Settings(morphloop_llm_timeout_seconds=JUDGE_LEASE_TTL.total_seconds() + 1)

    with pytest.raises(ValueError, match="judge lease TTL"):
        check_llm_timeout_under_lease(settings)


def test_timeout_under_the_lease_is_accepted() -> None:
    settings = Settings(morphloop_llm_timeout_seconds=JUDGE_LEASE_TTL.total_seconds() - 1)

    check_llm_timeout_under_lease(settings)  # does not raise


def test_app_refuses_to_start_with_an_unsafe_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_LLM_TIMEOUT_SECONDS", str(JUDGE_LEASE_TTL.total_seconds()))

    with pytest.raises(ValueError, match="judge lease TTL"):
        with TestClient(create_app()):
            pass


def test_app_starts_with_the_default_timeout() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
