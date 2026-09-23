"""litellm implementation of the LLMProvider Port (ADR-0013, ADR-0015, ADR-0016).

One adapter serves every provider. Switching provider is a pack change: the
pack declares ``model`` verbatim as litellm spells it (``"openai/gpt-5"``,
``"anthropic/claude-opus-4-1"``), and this adapter passes that string through
untouched. Credentials stay in the environment, where litellm reads them
(``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, ...); the harness never holds a key.

Structured-output mechanism
---------------------------
``litellm.completion(response_format={"type": "json_schema", "json_schema":
{"name": ..., "schema": ...}})`` -- the OpenAI-shaped ``response_format``,
which litellm translates per provider (for Anthropic,
``litellm/llms/anthropic/chat/transformation.py`` reads
``response_format["json_schema"]["schema"]`` and maps it onto the native
structured-output format, falling back to a forced tool call). The reply comes
back as JSON text in ``response.choices[0].message.content``.

Per ``contracts/schemas/llm/README.md`` ("Structured-output compatibility"),
the contract schemas already close every object and list every property, and
``LLMRequest.output_schema`` arrives with external ``$ref``s inlined by core,
so ``request.output_schema`` is sent unchanged -- no schema rewriting here.

``strict`` is deliberately not sent. OpenAI's strict mode accepts only a
subset of JSON Schema and rejects the whole request otherwise: it needs a
``type`` beside every ``const`` (``tutor.reply`` references), forbids
``uniqueItems`` (``evaluator.judgment`` supporting_event_ids,
``learner_model.update`` misconceptions) and requires every property in
``required`` (the ``common/skill-state.json`` exception the contract README
already calls out). Rewriting the schema to fit would make the adapter send
something weaker than the contract while the contract itself is frozen. The
README is explicit that a provider may ignore ``pattern``, ``const``,
``uniqueItems`` and friends and that only the harness-side validator is
authoritative for them -- which is exactly what a non-strict ``json_schema``
gives: the schema still steers the model, and ``_validate_output`` below plus
core's contract validation reject anything that does not conform (AC-E4).


Generation parameters come from the pack and are forwarded to
``litellm.completion`` as keyword arguments (ADR-0002: the harness holds no
defaults, not even ``max_tokens``).

Error mapping
-------------
The Port draws one non-output error, :class:`LLMError` ("transport, auth,
rate limit, etc."). litellm's exception classes share no base of their own
(they subclass the ``openai`` ones, and a few subclass ``Exception``
directly), so rather than enumerate a hierarchy that can drift, every failure
raised out of the completion call is wrapped into one :class:`LLMError` with
the original attached via ``raise ... from``. No retry is added here.

Output validation
-----------------
The Port only guarantees that ``LLMResponse.output`` is a JSON object; core
still validates it against the contract schema and applies the semantic rules
no schema expresses. This adapter additionally runs the parsed output through
``jsonschema`` against ``request.output_schema`` as a defense-in-depth check;
a failure raises :class:`LLMOutputError`, which core already treats as "no
usable output, no state change" (AC-E4).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Protocol, cast

import jsonschema
import litellm

from harness.core.ports import (
    JsonObject,
    LLMError,
    LLMOutputError,
    LLMRequest,
    LLMResponse,
)
from harness.core.ports.json_types import to_plain_object


class CompletionCallable(Protocol):
    """The one call this adapter makes: ``litellm.completion(**kwargs)``.

    Tests inject a stand-in of the same shape that returns real
    ``litellm.types.utils.ModelResponse`` objects (or raises a real litellm
    exception), so the response shape stays authentic with no network.
    """

    def __call__(self, **kwargs: Any) -> Any: ...


class LiteLLMProvider:
    """:class:`~harness.core.ports.LLMProvider` backed by litellm.

    ``completion`` defaults to :func:`litellm.completion`; pass one in to test
    without a network.
    """

    def __init__(self, *, completion: CompletionCallable | None = None) -> None:
        self._completion: CompletionCallable = completion or cast(
            CompletionCallable, litellm.completion
        )

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        llm = request.llm
        provider = _provider_of(llm.model)

        try:
            response = self._completion(
                model=llm.model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.role,
                        "schema": to_plain_object(request.output_schema),
                    },
                },
                **dict(llm.generation_parameters),
            )
        except Exception as exc:
            raise LLMError(f"litellm call to {llm.model!r} failed: {exc}") from exc

        output = _extract_output(response)
        _validate_output(output, request.output_schema)
        return LLMResponse(
            output=output,
            provenance=replace(
                llm,
                provider=provider,
                model=str(getattr(response, "model", None) or llm.model),
            ),
        )


def _provider_of(model: str) -> str:
    """The provider litellm routes ``model`` to, for the recorded provenance."""
    try:
        return str(litellm.get_llm_provider(model=model)[1])
    except Exception as exc:
        raise LLMError(f"no litellm provider for model {model!r}: {exc}") from exc


def _extract_output(response: Any) -> JsonObject:
    """Parse the structured-output message into a JSON object.

    Raises :class:`LLMOutputError` for every case the Port calls out: a
    refusal, a missing reply, and malformed or non-object JSON.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMOutputError("the LLM response contained no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise LLMOutputError(f"the LLM refused the request: {refusal}")

    content = getattr(message, "content", None)
    if not content:
        raise LLMOutputError(
            "the LLM response contained no content "
            f"(finish_reason={getattr(choice, 'finish_reason', None)!r})"
        )

    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"the LLM output was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LLMOutputError(f"the LLM output was not a JSON object (got {type(parsed).__name__})")
    return cast(JsonObject, parsed)


def _validate_output(output: JsonObject, schema: JsonObject) -> None:
    validator = jsonschema.Draft202012Validator(to_plain_object(schema))
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise LLMOutputError(f"the LLM output failed schema validation: {details}")
