"""`harness.cli.wiring.llm_provider` provider selection and production guard (#130)."""

from __future__ import annotations

import pytest

from harness.adapters.fake_llm import FakeDevLLMProvider
from harness.adapters.litellm import LiteLLMProvider
from harness.cli import wiring
from harness.cli.errors import CommandError


def test_default_is_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORPHLOOP_LLM_PROVIDER", raising=False)
    assert isinstance(wiring.llm_provider(), LiteLLMProvider)


def test_fake_opt_in_returns_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_LLM_PROVIDER", "fake")
    monkeypatch.delenv("MORPHLOOP_ENVIRONMENT", raising=False)
    assert isinstance(wiring.llm_provider(), FakeDevLLMProvider)


def test_fake_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_LLM_PROVIDER", "fake")
    monkeypatch.setenv("MORPHLOOP_ENVIRONMENT", "production")
    with pytest.raises(CommandError):
        wiring.llm_provider()
