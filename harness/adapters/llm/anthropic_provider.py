"""Anthropic implementation of the LLMProvider Port (ADR-0013, ADR-0015, ADR-0016).

Structured-output mechanism
----------------------------
The installed ``anthropic`` SDK (``pyproject.toml`` pins ``>=1.7.0``; the
``.venv`` has ``1.7.0``) exposes ``output_config.format`` on
``client.messages.create()``: ``anthropic.types.JSONOutputFormatParam`` takes
``{"type": "json_schema", "schema": <json schema>}`` and constrains the
model's reply to a single ``text`` content block containing JSON that
validates against the given schema (confirmed against
platform.claude.com/docs/en/build-with-claude/structured-outputs: ``required``
need not list every property, ``$ref``/``$defs`` are supported as long as they
are internal, and the response comes back as JSON text in a ``text`` block,
not a distinct block type).

This adapter calls ``client.messages.create(...)`` directly rather than
``client.messages.parse()``. ``.parse()`` only adds value
(``response.parsed_output``, via ``response.content[i].parsed_output``) when
given a Python/pydantic type through its ``output_format=`` argument; handed a
raw JSON Schema ``dict`` (which is what ``LLMRequest.output_schema`` carries,
per ``harness/core/ports/llm.py``) it degrades to exactly what ``.create()``
does, plus an unused code path (see
``anthropic/resources/messages/messages.py:Messages.parse`` --
``transformed_output_format`` stays ``NOT_GIVEN`` unless ``output_format`` is
a type). Tool-use-forced JSON was not used either: native structured output is
supported by this SDK version and needs no synthetic tool schema or
``tool_choice`` forcing.

Per ``contracts/schemas/llm/README.md`` ("Structured-output compatibility"),
every schema under ``contracts/schemas/llm/`` already closes every object
(``additionalProperties: false``) and lists every property in ``properties``,
and ``LLMRequest.output_schema`` arrives with external ``$ref``s already
inlined by core (see the ``LLMRequest`` docstring), so this adapter sends
``request.output_schema`` to the provider unchanged -- no schema rewriting is
needed for this SDK/API version.

Error mapping
--------------
``harness.core.ports.llm.LLMProvider`` exposes exactly one non-output error
type, :class:`~harness.core.ports.LLMError` ("transport, auth, rate limit,
etc."); the Port draws no finer distinction for callers, so every
``anthropic.AnthropicError`` (the SDK's common base for
``APIStatusError``/``APIConnectionError``/``APITimeoutError`` and their
subclasses) is wrapped into one :class:`LLMError`, preserving the original
exception via ``raise ... from exc``. The SDK's own ``max_retries`` (default
2, covering connection errors/408/409/429/5xx) is the only retry behaviour in
play; this adapter adds none of its own.

Output validation
-------------------
Per the ``LLMProvider`` Port docstring, this adapter's only *guaranteed*
contract is that ``LLMResponse.output`` is a JSON object parsed from the
provider's structured output -- semantic validation against learner-state
rules is Core's job. This adapter additionally runs the parsed output through
``jsonschema`` against ``request.output_schema`` as a defense-in-depth check
(the caller of this adapter, an orchestrator task, asked for it explicitly);
any failure raises :class:`~harness.core.ports.LLMOutputError`, which Core
already treats as "no parseable output, no state change" -- so this is a
strict superset of, not a substitute for, Core's own contract-schema
validation (which resolves ``$ref`` against the full ``contracts/`` registry
and enforces semantic rules no schema can express).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

import anthropic
import jsonschema

from harness.core.ports import (
    GenerationParameter,
    JsonObject,
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMRequest,
    LLMResponse,
)
from harness.core.ports.json_types import to_plain_object

#: ``LLMProvenance.provider`` value this adapter serves; wiring dispatches to
#: it by matching ``request.llm.provider`` against this constant.
PROVIDER_NAME = "anthropic"


class _MessagesResource(Protocol):
    """The one method this adapter calls on ``anthropic.Anthropic().messages``."""

    def create(self, **kwargs: Any) -> Any: ...


class _AnthropicClient(Protocol):
    """The slice of ``anthropic.Anthropic`` this adapter depends on.

    A real ``anthropic.Anthropic`` instance satisfies this structurally.
    Tests inject a minimal stand-in with the same shape and no network
    access; its ``create`` returns real ``anthropic.types.Message`` /
    ``anthropic.types.TextBlock`` instances (or raises a real
    ``anthropic.AnthropicError``), so the SDK-shape stays authentic without
    talking to a network.
    """

    @property
    def messages(self) -> _MessagesResource: ...


class AnthropicLLMProvider:
    """:class:`~harness.core.ports.LLMProvider` backed by the Anthropic Python SDK.

    The pack declares ``model`` and every generation parameter (ADR-0002);
    this adapter holds no default model name and no default parameters. It
    reads ``generation_parameters["max_tokens"]`` (required -- the SDK call
    needs it) and forwards every other declared parameter to the API via
    ``extra_body`` (the SDK's typed ``messages.create`` signature no longer
    lists e.g. ``temperature``/``top_p`` for current-generation models, but
    older models a pack may still declare -- e.g. dated snapshots -- accept
    them on the wire).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: _AnthropicClient | None = None,
    ) -> None:
        """Build a provider.

        ``api_key`` is taken as given (wiring is responsible for reading
        ``ANTHROPIC_API_KEY``; this class holds no environment lookup of its
        own). Pass ``client`` instead to inject a test double or a
        preconfigured SDK client; when both are omitted, construction fails
        fast with ``ValueError`` rather than silently building a client with
        no credentials.
        """
        if client is not None:
            self._client: _AnthropicClient = client
        elif api_key:
            # `anthropic.Anthropic` structurally satisfies `_AnthropicClient` at
            # runtime (its `.messages.create(...)` accepts every keyword this
            # adapter passes); mypy cannot see that because `Messages.create`
            # is declared with concrete keyword parameters rather than
            # `**kwargs`, so a real SDK client is not a *structural* match for
            # a `**kwargs: Any` Protocol method.
            self._client = cast(_AnthropicClient, anthropic.Anthropic(api_key=api_key))
        else:
            raise ValueError("AnthropicLLMProvider needs either api_key or an injected client")

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        max_tokens = _require_max_tokens(request.llm.generation_parameters)
        system_text, sdk_messages = _split_messages(request.messages)
        extra_params = {
            key: value
            for key, value in request.llm.generation_parameters.items()
            if key != "max_tokens"
        }

        create_kwargs: dict[str, Any] = {
            "model": request.llm.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": to_plain_object(request.output_schema),
                }
            },
        }
        if system_text is not None:
            create_kwargs["system"] = system_text
        if extra_params:
            create_kwargs["extra_body"] = extra_params

        try:
            response = self._client.messages.create(**create_kwargs)
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Anthropic API call failed: {exc}") from exc

        output = _extract_output(response)
        _validate_output(output, request.output_schema)
        return LLMResponse(output=output, provenance=request.llm)


def _require_max_tokens(generation_parameters: Mapping[str, GenerationParameter]) -> int:
    value = generation_parameters.get("max_tokens")
    # bool is a subclass of int in Python; a pack that declared `"max_tokens": true`
    # is a config error, not a token count, so it is rejected alongside non-ints.
    if not isinstance(value, int) or isinstance(value, bool):
        raise LLMError(
            "generation_parameters must declare an integer 'max_tokens' "
            f"(the pack declares every generation parameter, ADR-0002); got {value!r}"
        )
    return value


def _split_messages(
    messages: Sequence[LLMMessage],
) -> tuple[str | None, list[dict[str, str]]]:
    """Pull any leading ``system``-role messages into a top-level ``system`` string.

    Anthropic requires the first entry of ``messages`` to be a ``user`` turn;
    a ``system``-role message is only valid mid-conversation (must follow a
    ``user`` turn) on models that support it. Core assembles a request's
    system prompt as the leading message(s) (``LLMRequest`` carries a flat
    ``messages`` sequence with no separate system field), so this adapter
    extracts that leading run into the ``system`` parameter, where every
    model accepts it. Any ``system``-role message that is not leading is left
    in place in ``messages`` and passed through unchanged.
    """
    remaining = list(messages)
    system_parts: list[str] = []
    while remaining and remaining[0].role == "system":
        system_parts.append(remaining.pop(0).content)
    sdk_messages = [{"role": message.role, "content": message.content} for message in remaining]
    system_text = "\n\n".join(system_parts) if system_parts else None
    return system_text, sdk_messages


def _extract_output(response: Any) -> JsonObject:
    """Parse the provider's structured-output text block into a JSON object.

    Raises :class:`LLMOutputError` for every case the Port docstring calls
    out explicitly: a refusal, no text block, and malformed/non-object JSON.
    """
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        raise LLMOutputError(f"Anthropic refused the request: {stop_details!r}")

    content = getattr(response, "content", None) or []
    text_parts = [block.text for block in content if getattr(block, "type", None) == "text"]
    if not text_parts:
        raise LLMOutputError(
            f"Anthropic response contained no text content block (stop_reason={stop_reason!r})"
        )
    raw_text = "".join(text_parts)

    try:
        parsed: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"Anthropic output was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LLMOutputError(
            f"Anthropic output was not a JSON object (got {type(parsed).__name__})"
        )
    return cast(JsonObject, parsed)


def _validate_output(output: JsonObject, schema: JsonObject) -> None:
    validator = jsonschema.Draft202012Validator(to_plain_object(schema))
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise LLMOutputError(f"Anthropic output failed schema validation: {details}")
