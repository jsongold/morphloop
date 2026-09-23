"""Tests for the litellm LLMProvider adapter (ADR-0013, ADR-0015, ADR-0016).

No network access: every test injects a fake completion callable that returns
real ``litellm.types.utils.ModelResponse`` instances (or raises a real litellm
exception) built in-process, so the adapter's runtime attribute access
(``response.choices[0].message.content``, ``response.model``, ...) is
exercised against genuine shapes without ever calling an API.
"""

from __future__ import annotations

from typing import Any

import httpx
import litellm
import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from harness.adapters.litellm import LiteLLMProvider
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
        "provider": "openai",
        "model": "openai/gpt-5",
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


def _response(
    content: str | None,
    *,
    model: str = "gpt-5-2025-08-07",
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> ModelResponse:
    message = Message(content=content, role="assistant")
    if refusal is not None:
        message.refusal = refusal  # type: ignore[attr-defined]
    return ModelResponse(
        id="chatcmpl-test",
        choices=[Choices(finish_reason=finish_reason, index=0, message=message)],
        model=model,
    )


class _FakeCompletion:
    """Stand-in for ``litellm.completion``: no network, canned replies."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("_FakeCompletion: no scripted response left")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _rate_limit_error() -> litellm.RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return litellm.RateLimitError(
        "slow down",
        llm_provider="openai",
        model="gpt-5",
        response=httpx.Response(429, request=request),
    )


# --- Construction ---------------------------------------------------------


def test_satisfies_llm_provider_protocol() -> None:
    provider: LLMProvider = LiteLLMProvider(completion=_FakeCompletion([]))
    assert provider is not None


def test_defaults_to_litellm_completion() -> None:
    # No key needed: nothing is called, the default callable is only bound.
    assert LiteLLMProvider() is not None


# --- Success --------------------------------------------------------------


def test_complete_structured_returns_parsed_output() -> None:
    completion = _FakeCompletion([_response('{"rationale": "good work", "confidence": 0.9}')])
    provider = LiteLLMProvider(completion=completion)

    response = provider.complete_structured(_request())

    assert isinstance(response, LLMResponse)
    assert response.output == {"rationale": "good work", "confidence": 0.9}


def test_complete_structured_maps_request_onto_the_litellm_call() -> None:
    completion = _FakeCompletion([_response('{"rationale": "ok", "confidence": 0.5}')])
    provider = LiteLLMProvider(completion=completion)

    provider.complete_structured(_request())

    assert len(completion.calls) == 1
    call = completion.calls[0]
    # the pack's model string goes through verbatim ...
    assert call["model"] == "openai/gpt-5"
    # ... messages keep their roles, system message included ...
    assert call["messages"] == [
        {"role": "system", "content": "You are a strict evaluator."},
        {"role": "user", "content": "Did the learner pass?"},
    ]
    # ... the schema goes untouched into response_format ...
    assert call["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "evaluator", "schema": SCHEMA, "strict": True},
    }
    # ... and every generation parameter the pack declares is forwarded.
    assert call["max_tokens"] == 1024
    assert call["temperature"] == 0


@pytest.mark.parametrize(
    ("model", "provider_name"),
    [
        ("openai/gpt-5", "openai"),
        ("anthropic/claude-opus-4-1", "anthropic"),
        ("gpt-4o", "openai"),
    ],
)
def test_pack_model_string_selects_the_provider(model: str, provider_name: str) -> None:
    completion = _FakeCompletion([_response('{"rationale": "ok", "confidence": 0.5}', model=model)])
    provider = LiteLLMProvider(completion=completion)

    response = provider.complete_structured(_request(llm=_provenance(model=model)))

    assert completion.calls[0]["model"] == model
    assert response.provenance.provider == provider_name


def test_provenance_records_the_model_actually_used_and_the_pack_prompt() -> None:
    completion = _FakeCompletion(
        [_response('{"rationale": "ok", "confidence": 0.5}', model="gpt-5-2025-08-07")]
    )
    provider = LiteLLMProvider(completion=completion)
    request = _request()

    provenance = provider.complete_structured(request).provenance

    assert provenance.to_dict() == {
        "provider": "openai",
        "model": "gpt-5-2025-08-07",
        "prompt_id": "evaluator-judgment",
        "prompt_version": "1",
        "generation_parameters": {"max_tokens": 1024, "temperature": 0},
    }


def test_provenance_falls_back_to_the_requested_model() -> None:
    completion = _FakeCompletion([_response('{"rationale": "ok", "confidence": 0.5}', model="")])
    provider = LiteLLMProvider(completion=completion)

    provenance = provider.complete_structured(_request()).provenance

    assert provenance.model == "openai/gpt-5"


# --- Schema-invalid / missing output -> LLMOutputError --------------------


def test_complete_structured_schema_invalid_output_raises_llm_output_error() -> None:
    completion = _FakeCompletion([_response('{"rationale": "missing confidence"}')])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError, match="confidence"):
        provider.complete_structured(_request())


def test_complete_structured_malformed_json_raises_llm_output_error() -> None:
    completion = _FakeCompletion([_response("not json at all")])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError):
        provider.complete_structured(_request())


def test_complete_structured_non_object_json_raises_llm_output_error() -> None:
    completion = _FakeCompletion([_response("[1, 2, 3]")])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError, match="JSON object"):
        provider.complete_structured(_request())


def test_complete_structured_no_content_raises_llm_output_error() -> None:
    completion = _FakeCompletion([_response(None, finish_reason="length")])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError, match="no content"):
        provider.complete_structured(_request())


def test_complete_structured_refusal_raises_llm_output_error() -> None:
    completion = _FakeCompletion([_response(None, refusal="I cannot help with that")])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError, match="refused"):
        provider.complete_structured(_request())


def test_complete_structured_no_choices_raises_llm_output_error() -> None:
    empty = ModelResponse(id="chatcmpl-empty", choices=[], model="gpt-5")
    completion = _FakeCompletion([empty])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMOutputError, match="no choices"):
        provider.complete_structured(_request())


# --- Transport/API errors -> LLMError --------------------------------------


def test_complete_structured_api_error_raises_llm_error() -> None:
    original = _rate_limit_error()
    provider = LiteLLMProvider(completion=_FakeCompletion([original]))

    with pytest.raises(LLMError) as excinfo:
        provider.complete_structured(_request())

    assert not isinstance(excinfo.value, LLMOutputError)
    assert excinfo.value.__cause__ is original


def test_complete_structured_connection_error_raises_llm_error() -> None:
    original = litellm.APIConnectionError(message="boom", llm_provider="openai", model="gpt-5")
    provider = LiteLLMProvider(completion=_FakeCompletion([original]))

    with pytest.raises(LLMError):
        provider.complete_structured(_request())


def test_unroutable_model_raises_llm_error_before_any_call() -> None:
    completion = _FakeCompletion([])
    provider = LiteLLMProvider(completion=completion)

    with pytest.raises(LLMError, match="no litellm provider"):
        provider.complete_structured(_request(llm=_provenance(model="not-a-real-model")))

    assert completion.calls == []
