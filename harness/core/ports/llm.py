"""LLM provider Port (ADR-0013, ADR-0015, ADR-0016).

One provider-neutral structured-output call. Provider SDK types never cross
this Port; each provider SDK is a technical Adapter under
``harness/adapters/llm*``.

Where validation happens:

- The adapter guarantees only that ``LLMResponse.output`` is a JSON object
  parsed from the provider's structured output. It may pass
  ``LLMRequest.output_schema`` to the provider, transforming it as the provider
  requires (``contracts/schemas/llm/README.md``), but it does not validate the
  output against it and knows nothing about learner-state semantics.
- Core validates every output against the contract schema named by
  ``output_schema_id`` and applies the semantic rules no schema can express
  (e.g. ``evidence_ids`` must be a subset of the evidence given in the call)
  before the output may affect state. An output that fails changes no state
  (AC-E4).

The full prompt (``messages``) is a system log and never appears in an event;
only the output and :class:`LLMProvenance` do (ADR-0016).

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from harness.core.ports.json_types import JsonObject, PlainJson

type LLMRole = Literal["tutor", "evaluator", "learner_model", "generator", "memo_summarizer"]
type MessageRole = Literal["system", "user", "assistant"]
type GenerationParameter = str | int | float | bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMProvenance:
    """LLM settings, identical in shape to ``common/provenance.json#/$defs/llm``.

    The pack declares exactly this block as ``registry.<role>.llm``, core sends
    it with the request, and the adapter echoes what it actually sent, so events
    copy it verbatim. The pack supplies every generation parameter; the
    adapter adds no defaults (ADR-0002).
    """

    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    generation_parameters: Mapping[str, GenerationParameter]

    def to_dict(self) -> dict[str, PlainJson]:
        """Return the wire form, valid against ``provenance.json#/$defs/llm``."""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "generation_parameters": dict(self.generation_parameters),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMMessage:
    """One chat message. Core assembles these within the pack's context budget."""

    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMRequest:
    """A structured-output call for one logical role.

    ``output_schema`` is the contract schema with external ``$ref``s inlined
    (bundled by core); ``output_schema_id`` is its ``$id`` under
    ``contracts/schemas/llm/``, which core validates the output against.
    """

    role: LLMRole
    llm: LLMProvenance
    messages: Sequence[LLMMessage]
    output_schema_id: str
    output_schema: JsonObject

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("an LLM request needs at least one message")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMResponse:
    """Parsed structured output plus the provenance to record on the event.

    ``output`` is unvalidated against the output schema (see module docstring).
    """

    output: JsonObject
    provenance: LLMProvenance


class LLMError(Exception):
    """Base class for LLM adapter failures (transport, auth, rate limit, etc.)."""


class LLMOutputError(LLMError):
    """The provider returned no parseable JSON object (refusal, truncation,
    malformed JSON). Core treats this like a validation failure: no state change."""


class LLMProvider(Protocol):
    """Provider-neutral structured-output LLM call.

    Synchronous: v0.1 calls it from request handlers that FastAPI runs in its
    thread pool (see ``harness.core.ports``). Wiring picks the adapter whose
    provider matches ``request.llm.provider``; an adapter raises
    :class:`LLMError` for a provider it does not serve.
    """

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        """Run one call and return the parsed JSON object output.

        Raises :class:`LLMOutputError` when no JSON object can be parsed and
        :class:`LLMError` for other failures. Never retries silently with
        different parameters; any retry keeps ``request.llm`` unchanged.
        """
        ...
