"""Tests for the Anthropic LLMProvider adapter (ADR-0013, ADR-0015, ADR-0016).

No network access: every test injects a fake client whose ``messages.create``
returns real ``anthropic.types.Message``/``TextBlock`` instances (or raises a
real ``anthropic.AnthropicError`` subclass) built in-process, so the adapter's
runtime attribute access (``response.content``, ``response.stop_reason``, ...)
is exercised against genuine SDK shapes without ever calling the API.
"""

from __future__ import annotations

from typing import Any

import anthropic
import anthropic.types as at
import httpx2
import pytest

from harness.adapters.llm import AnthropicLLMProvider
from harness.core.ports import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

# A small, self-contained output schema representative of a bundled
# LLMRequest.output_schema (external $refs already inlined by core).
SCHEMA = {
    "type": "object",
    "required": ["rationale", "confidence"],
    "properties": {
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}


def _provenance(**overrides: Any) -> LLMProvenance:
    fields: dict[str, Any] = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "prompt_id": "evaluator-judgment",
        "prompt_version": "1",
        "generation_parameters": {"max_tokens": 1024, "temperature": 0},
    }
    fields.update(overrides)
    return LLMProvenance(**fields)


def _request(**overrides: Any) -> LLMRequest:
    fields: dict[str, Any] = {
        "role": "evaluator",
        "llm": _provenance(),
        "messages": [
            LLMMessage(role="system", content="You are a strict evaluator."),
            LLMMessage(role="user", content="Did the learner pass?"),
        ],
        "output_schema_id": "https://morphloop.dev/contracts/schemas/llm/evaluator.judgment/1.json",
        "output_schema": SCHEMA,
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def _text_message(
    text: str,
    *,
    stop_reason: str = "end_turn",
    stop_details: at.RefusalStopDetails | None = None,
) -> at.Message:
    return at.Message(
        id="msg_test",
        content=[at.TextBlock(type="text", text=text)],
        model="claude-sonnet-4-5-20250929",
        role="assistant",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        stop_details=stop_details,
        usage=at.Usage(input_tokens=10, output_tokens=5),
        type="message",
    )


class _FakeMessages:
    """Stand-in for ``anthropic.Anthropic().messages``: no network, canned replies."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("_FakeMessages: no scripted response left")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeAnthropicClient:
    def __init__(self, script: list[Any]) -> None:
        self.messages = _FakeMessages(script)


def _rate_limit_error() -> anthropic.RateLimitError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, request=request, json={"error": {"message": "slow down"}})
    return anthropic.RateLimitError("rate limited", response=response, body=None)


# --- Construction -------------------------------------------------------


def test_satisfies_llm_provider_protocol() -> None:
    provider: LLMProvider = AnthropicLLMProvider(client=_FakeAnthropicClient([]))
    assert provider is not None


def test_requires_api_key_or_client() -> None:
    with pytest.raises(ValueError):
        AnthropicLLMProvider()


# --- Success --------------------------------------------------------------


def test_complete_structured_returns_parsed_output_and_echoes_provenance() -> None:
    client = _FakeAnthropicClient([_text_message('{"rationale": "good work", "confidence": 0.9}')])
    provider = AnthropicLLMProvider(client=client)
    request = _request()

    response = provider.complete_structured(request)

    assert isinstance(response, LLMResponse)
    assert response.output == {"rationale": "good work", "confidence": 0.9}
    assert response.provenance == request.llm


def test_complete_structured_maps_request_onto_the_sdk_call() -> None:
    client = _FakeAnthropicClient([_text_message('{"rationale": "ok", "confidence": 0.5}')])
    provider = AnthropicLLMProvider(client=client)
    request = _request()

    provider.complete_structured(request)

    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-4-5-20250929"
    assert call["max_tokens"] == 1024
    # the leading system-role message is pulled out of `messages` ...
    assert call["system"] == "You are a strict evaluator."
    assert call["messages"] == [{"role": "user", "content": "Did the learner pass?"}]
    # ... the schema goes untouched into output_config.format ...
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    # ... and every generation parameter besides max_tokens is forwarded.
    assert call["extra_body"] == {"temperature": 0}


def test_complete_structured_omits_system_when_no_leading_system_message() -> None:
    client = _FakeAnthropicClient([_text_message('{"rationale": "ok", "confidence": 0.5}')])
    provider = AnthropicLLMProvider(client=client)
    request = _request(messages=[LLMMessage(role="user", content="Just a question.")])

    provider.complete_structured(request)

    call = client.messages.calls[0]
    assert "system" not in call
    assert call["messages"] == [{"role": "user", "content": "Just a question."}]


def test_complete_structured_omits_extra_body_when_no_extra_generation_parameters() -> None:
    client = _FakeAnthropicClient([_text_message('{"rationale": "ok", "confidence": 0.5}')])
    provider = AnthropicLLMProvider(client=client)
    request = _request(llm=_provenance(generation_parameters={"max_tokens": 512}))

    provider.complete_structured(request)

    assert "extra_body" not in client.messages.calls[0]


# --- Schema-invalid / missing output -> LLMOutputError --------------------


def test_complete_structured_schema_invalid_output_raises_llm_output_error() -> None:
    client = _FakeAnthropicClient([_text_message('{"rationale": "missing confidence"}')])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMOutputError, match="confidence"):
        provider.complete_structured(_request())


def test_complete_structured_malformed_json_raises_llm_output_error() -> None:
    client = _FakeAnthropicClient([_text_message("not json at all")])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMOutputError):
        provider.complete_structured(_request())


def test_complete_structured_non_object_json_raises_llm_output_error() -> None:
    client = _FakeAnthropicClient([_text_message("[1, 2, 3]")])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMOutputError, match="JSON object"):
        provider.complete_structured(_request())


def test_complete_structured_no_text_block_raises_llm_output_error() -> None:
    message = at.Message(
        id="msg_no_text",
        content=[],
        model="claude-sonnet-4-5-20250929",
        role="assistant",
        stop_reason="end_turn",  # type: ignore[arg-type]
        stop_details=None,
        usage=at.Usage(input_tokens=10, output_tokens=0),
        type="message",
    )
    client = _FakeAnthropicClient([message])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMOutputError, match="no text content block"):
        provider.complete_structured(_request())


def test_complete_structured_refusal_raises_llm_output_error() -> None:
    details = at.RefusalStopDetails(type="refusal", category="cyber", explanation="nope")
    message = _text_message("", stop_reason="refusal", stop_details=details)
    client = _FakeAnthropicClient([message])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMOutputError, match="refused"):
        provider.complete_structured(_request())


# --- Transport/API errors -> LLMError --------------------------------------


def test_complete_structured_api_error_raises_llm_error() -> None:
    original = _rate_limit_error()
    client = _FakeAnthropicClient([original])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMError) as excinfo:
        provider.complete_structured(_request())

    assert not isinstance(excinfo.value, LLMOutputError)
    assert excinfo.value.__cause__ is original


def test_complete_structured_connection_error_raises_llm_error() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    original = anthropic.APIConnectionError(message="boom", request=request)
    client = _FakeAnthropicClient([original])
    provider = AnthropicLLMProvider(client=client)

    with pytest.raises(LLMError):
        provider.complete_structured(_request())


# --- generation_parameters config errors -> LLMError -----------------------


def test_complete_structured_missing_max_tokens_raises_llm_error() -> None:
    client = _FakeAnthropicClient([])
    provider = AnthropicLLMProvider(client=client)
    request = _request(llm=_provenance(generation_parameters={"temperature": 0}))

    with pytest.raises(LLMError, match="max_tokens"):
        provider.complete_structured(request)

    # fails fast, before any SDK call is attempted
    assert client.messages.calls == []


def test_complete_structured_non_integer_max_tokens_raises_llm_error() -> None:
    client = _FakeAnthropicClient([])
    provider = AnthropicLLMProvider(client=client)
    request = _request(llm=_provenance(generation_parameters={"max_tokens": "a lot"}))

    with pytest.raises(LLMError, match="max_tokens"):
        provider.complete_structured(request)
