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

Tool calling (v0.2.0) is a second, separate Port, :class:`LLMToolProvider`,
so existing :class:`LLMProvider` implementations stay valid unchanged. The
model may answer with text, with tool calls, or both; core runs each tool and
sends the result back as an :class:`LLMToolResult` after the assistant
:class:`LLMMessage` that carried the calls. The adapter guarantees each
:class:`LLMToolCall` names a declared tool and carries a JSON object of
arguments; core still validates arguments before a tool changes state.

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
type ToolChoice = Literal["auto", "required", "none"]


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
class LLMToolCall:
    """One tool call the model asked for. ``id`` pairs it with its result."""

    id: str
    name: str
    arguments: JsonObject

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("a tool call needs a non-empty id and name")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMMessage:
    """One chat message. Core assembles these within the pack's context budget.

    ``tool_calls`` is set only on an assistant message replayed into a tool
    conversation, so the following :class:`LLMToolResult` s have their calls.
    """

    role: MessageRole
    content: str
    tool_calls: Sequence[LLMToolCall] = ()

    def __post_init__(self) -> None:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only an assistant message can carry tool calls")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMToolResult:
    """The result of running one tool call, sent back to the model."""

    tool_call_id: str
    content: str

    def __post_init__(self) -> None:
        if not self.tool_call_id:
            raise ValueError("a tool result needs the id of its tool call")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMTool:
    """A tool the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: JsonObject

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a tool needs a non-empty name")


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


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMToolRequest:
    """A tool-calling chat turn. ``tool_choice`` is required (no defaults)."""

    llm: LLMProvenance
    messages: Sequence[LLMMessage | LLMToolResult]
    tools: Sequence[LLMTool]
    tool_choice: ToolChoice

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("an LLM request needs at least one message")
        if not self.tools:
            raise ValueError("a tool request needs at least one tool")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError(f"tool names must be unique, got {names}")


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMToolResponse:
    """The model's reply: text, tool calls, or both, plus provenance."""

    content: str | None
    tool_calls: Sequence[LLMToolCall]
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


class LLMToolProvider(Protocol):
    """Provider-neutral tool-calling chat turn (synchronous, like :class:`LLMProvider`)."""

    def complete_with_tools(self, request: LLMToolRequest) -> LLMToolResponse:
        """Run one turn. Raises :class:`LLMOutputError` for a call to an
        undeclared tool or non-object arguments, :class:`LLMError` otherwise.
        """
        ...
