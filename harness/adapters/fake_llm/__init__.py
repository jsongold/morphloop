"""Deterministic fake implementation of the LLM Ports, for dev-stack / local E2E
runs with no provider key (#130).

Selected by ``harness.cli.wiring.llm_provider`` when
``MORPHLOOP_LLM_PROVIDER=fake`` -- the one place that choice is made, so the
production guard lives in one spot instead of one copy per call site (chat,
notebook generation, the v0.1 loop, the CLI).

Structured-output shape: rather than script one canned reply per schema (which
would need updating for every pack schema, present or future), this synthesizes
the minimal instance a given JSON Schema allows -- fill only the required
properties, shortest valid string/array/number for each -- then validates it
with the same ``jsonschema`` validator a real adapter runs defensively
(``harness/adapters/litellm/provider.py``). Every ``$ref`` is already inlined by
``ContractSchemas.bundle`` before an :class:`~harness.core.ports.llm.LLMRequest`
reaches an adapter, so no resolver is needed here.

# ponytail: handles the schema shapes contracts/schemas/llm/* actually use
# (object/array/string/number/boolean, enum/const, minLength/minItems, id
# patterns). A schema needing a `format` value or a real regex match beyond the
# `prefix_XXX` / uuid id conventions raises `FakeLLMProviderError` instead of
# guessing; extend `_fake_string` if a new pattern shape shows up.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import jsonschema

from harness.core.ports.json_types import PlainJson, to_plain_object
from harness.core.ports.llm import (
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMToolRequest,
    LLMToolResponse,
)

FAKE_REPLY_TEXT = (
    "[fake LLM] This is a deterministic dev-stack reply "
    "(MORPHLOOP_LLM_PROVIDER=fake); no real model was called."
)

_PREFIXED_ID = re.compile(r"^\^([A-Za-z]+)_\[")
_HEX_GROUPS = re.compile(r"0-9a-f")


class FakeLLMProviderError(LLMError):
    """The fake provider has no rule to synthesize a value for this schema."""


class FakeDevLLMProvider:
    """:class:`~harness.core.ports.llm.LLMProvider` and ``LLMToolProvider`` in one.

    No network, no key, fully deterministic. Never wired in production
    settings; see ``harness.cli.wiring.llm_provider``.
    """

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        schema = to_plain_object(request.output_schema)
        output = _minimal_instance(schema)
        if not isinstance(output, dict):
            raise FakeLLMProviderError(
                f"the fake LLM cannot answer {request.role!r}: "
                f"{request.output_schema_id} is not an object schema"
            )
        _assert_valid(output, schema, role=request.role)
        return LLMResponse(output=output, provenance=replace(request.llm, provider="fake"))

    def complete_with_tools(self, request: LLMToolRequest) -> LLMToolResponse:
        return LLMToolResponse(
            content=FAKE_REPLY_TEXT,
            tool_calls=(),
            provenance=replace(request.llm, provider="fake"),
        )


def _assert_valid(instance: PlainJson, schema: dict[str, PlainJson], *, role: str) -> None:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise FakeLLMProviderError(f"fake LLM output for {role!r} failed its own schema: {details}")


def _minimal_instance(schema: dict[str, Any], *, index: int = 0) -> PlainJson:
    """The simplest value ``schema`` allows: for ``object``, only its required properties."""
    if "const" in schema:
        return schema["const"]  # type: ignore[no-any-return]
    if "enum" in schema:
        return schema["enum"][0]  # type: ignore[no-any-return]
    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            return _minimal_instance(schema[combinator][0], index=index)

    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])

    # `allOf` with no `type`/`properties` of its own is pure composition (merge the
    # branches and start over); an `allOf` alongside a `type` (a pack schema's
    # `if`/`then`/`else` refinement, e.g. drill-item.json) is left alone -- filling
    # only the required top-level properties already satisfies a conditional whose
    # `then` adds a property this minimal instance never includes.
    if kind is None and "properties" not in schema and "allOf" in schema:
        merged: dict[str, Any] = {}
        for branch in schema["allOf"]:
            merged.update(branch)
        return _minimal_instance(merged, index=index)

    if kind == "object" or (kind is None and "properties" in schema):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {
            name: _minimal_instance(properties[name], index=index)
            for name in required
            if name in properties
        }
    if kind == "array":
        min_items = schema.get("minItems", 0)
        item_schema = schema.get("items", {})
        return [_minimal_instance(item_schema, index=i) for i in range(min_items)]
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        minimum = schema.get("minimum", 0)
        return minimum if isinstance(minimum, int | float) else 0
    if kind == "null":
        return None
    if kind == "string" or kind is None:
        return _fake_string(schema, index=index)
    raise FakeLLMProviderError(f"the fake LLM has no rule for schema type {kind!r}")


def _fake_string(schema: dict[str, Any], *, index: int) -> str:
    pattern = schema.get("pattern")
    if pattern:
        prefixed = _PREFIXED_ID.match(pattern)
        if prefixed:
            return f"{prefixed.group(1)}_fake{index:04d}"
        if "-" in pattern and _HEX_GROUPS.search(pattern):
            return f"00000000-0000-0000-0000-{index:012d}"
    value = f"fake{index}" if index else "fake"
    min_length = schema.get("minLength", 0)
    if len(value) < min_length:
        value += "x" * (min_length - len(value))
    return value
