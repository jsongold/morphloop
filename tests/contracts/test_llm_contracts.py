"""Contract tests for contracts/schemas/llm (ADR-0013, ADR-0014, ADR-0016).

Validates the structured LLM output schemas, their provider-compatibility
conventions (see contracts/schemas/llm/README.md), example outputs under
tests/contracts/fixtures/llm/, and that each output maps onto the event payload
the harness records it into.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, load_schema, validate

ID_BASE = "https://morphloop.dev/contracts/"
LLM_DIR = CONTRACTS_DIR / "schemas" / "llm"
LLM_SCHEMAS = sorted(LLM_DIR.rglob("*.json"))

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
EVENT_FIXTURES = Path(__file__).parent / "fixtures" / "events" / "valid"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

LEARNER_MODEL = "schemas/llm/learner_model.update/1.json"
EVALUATOR = "schemas/llm/evaluator.judgment/1.json"
TUTOR = "schemas/llm/tutor.reply/1.json"


def _payload(event_type: str) -> str:
    return f"schemas/events/payloads/{event_type}/1.json"


# Keywords avoided so a bundled schema stays usable as a provider structured-output schema.
UNSUPPORTED_KEYWORDS = {
    "oneOf",
    "allOf",
    "not",
    "if",
    "then",
    "else",
    "patternProperties",
    "dependentRequired",
    "dependentSchemas",
    "unevaluatedProperties",
    "unevaluatedItems",
    "propertyNames",
    "prefixItems",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    return path.relative_to(CONTRACTS_DIR).as_posix()


def _subschemas(node: Any) -> Iterator[dict[str, Any]]:
    """Every object-valued node of a schema document, without following $ref."""
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key in ("properties", "$defs"):
                for sub in value.values():
                    yield from _subschemas(sub)
            elif key in ("items", "anyOf"):
                yield from _subschemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from _subschemas(item)


def _examples(schema: str) -> list[dict[str, Any]]:
    return [c["instance"] for c in map(_load, VALID) if c["schema"] == schema]


def _event(event_type: str) -> dict[str, Any]:
    event: dict[str, Any] = _load(EVENT_FIXTURES / f"{event_type}.v1.json")
    return event


def test_llm_schemas_exist() -> None:
    assert LLM_SCHEMAS


@pytest.mark.parametrize("path", LLM_SCHEMAS, ids=_rel)
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("path", LLM_SCHEMAS, ids=_rel)
def test_schema_id_matches_file_path(path: Path) -> None:
    assert _load(path)["$id"] == ID_BASE + _rel(path)


@pytest.mark.parametrize("path", LLM_SCHEMAS, ids=_rel)
def test_layout_is_output_name_slash_major_version(path: Path) -> None:
    rel = path.relative_to(LLM_DIR)
    assert len(rel.parts) == 2, rel
    assert re.fullmatch(r"[a-z][a-z_]*(\.[a-z][a-z_]*)+", rel.parts[0]), rel
    assert re.fullmatch(r"[1-9][0-9]*", path.stem), rel


@pytest.mark.parametrize("path", LLM_SCHEMAS, ids=_rel)
def test_structured_output_conventions(path: Path) -> None:
    schema = _load(path)
    assert schema["type"] == "object"
    for node in _subschemas(schema):
        used = UNSUPPORTED_KEYWORDS & node.keys()
        assert not used, f"{used} in {node.get('title', node)}"
        if "properties" in node:
            # Closed objects with every property required (optional = nullable).
            assert node.get("additionalProperties") is False, node.get("title", node)
            assert set(node["required"]) == set(node["properties"]), node.get("title", node)
        if node.get("type") == "object":
            assert "properties" in node, f"open object: {node}"


@pytest.mark.parametrize("path", LLM_SCHEMAS, ids=_rel)
def test_every_schema_has_a_valid_example(path: Path) -> None:
    assert _examples(_rel(path)), f"no valid example for {_rel(path)}"


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_output(path: Path) -> None:
    case = _load(path)
    assert path.stem.startswith(case["schema"].split("/")[2] + ".v1.")
    validate(case["instance"], case["schema"])


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_output_is_rejected(path: Path) -> None:
    case = _load(path)
    with pytest.raises(ContractViolation) as excinfo:
        validate(case["instance"], case["schema"])
    reported = {
        line.split(": ", 1)[0] for line in str(excinfo.value).splitlines()[1:] if ": " in line
    }
    for expected in case["expected_error_paths"]:
        assert expected in reported, f"{expected} not in {sorted(reported)}"


# --- Mapping onto event payloads: output fields are copied verbatim, the harness adds the rest.


@pytest.mark.parametrize(
    ("llm_schema", "event_payload"),
    [
        (LEARNER_MODEL, _payload("learner_skill.updated")),
        (TUTOR, _payload("assistant.message_generated")),
    ],
)
def test_output_fields_are_payload_fields(llm_schema: str, event_payload: str) -> None:
    assert set(load_schema(llm_schema)["properties"]) <= set(
        load_schema(event_payload)["properties"]
    )


def test_evaluator_fields_are_payload_fields() -> None:
    evaluator = load_schema(EVALUATOR)["properties"]
    evaluation = set(load_schema(_payload("evaluation.completed"))["properties"])
    evidence = set(load_schema(_payload("evidence.created"))["properties"])
    assert set(evaluator) - {"evidence"} <= evaluation
    assert set(evaluator["evidence"]["items"]["properties"]) <= evidence


@pytest.mark.parametrize("output", _examples(LEARNER_MODEL))
def test_learner_model_output_embeds_into_learner_skill_updated(output: dict[str, Any]) -> None:
    event = _event("learner_skill.updated")
    harness_fields = {k: event["payload"][k] for k in ("pack_id", "skill_id", "previous")}
    payload = {**harness_fields, **output, "provenance": event["payload"]["provenance"]}
    validate(payload, _payload("learner_skill.updated"))
    validate({**event, "payload": payload}, "schemas/events/envelope/stored.json")


def test_learner_skill_updated_example_is_a_learner_model_output() -> None:
    # The recorded example payload, minus harness-added fields, is itself a valid output.
    payload = _event("learner_skill.updated")["payload"]
    output = {k: payload[k] for k in load_schema(LEARNER_MODEL)["properties"]}
    validate(output, LEARNER_MODEL)


@pytest.mark.parametrize("output", _examples(EVALUATOR))
def test_evaluator_output_embeds_into_evaluation_and_evidence(output: dict[str, Any]) -> None:
    evaluation = copy.deepcopy(_event("evaluation.completed")["payload"])
    evaluation.update(success=output["success"], rationale=output["rationale"])
    validate(evaluation, _payload("evaluation.completed"))
    for n, item in enumerate(output["evidence"], start=1):
        evidence = {
            "evidence_id": f"ev_01J9ZQ4EV9{n:04d}",
            "evaluation_id": evaluation["evaluation_id"],
            **item,
        }
        validate(evidence, _payload("evidence.created"))


@pytest.mark.parametrize("output", _examples(TUTOR))
def test_tutor_output_embeds_into_assistant_message_generated(output: dict[str, Any]) -> None:
    event = _event("assistant.message_generated")["payload"]
    harness_fields = {
        k: event[k] for k in ("message_id", "thread_id", "in_reply_to", "mode", "provenance")
    }
    validate({**harness_fields, **output}, _payload("assistant.message_generated"))
