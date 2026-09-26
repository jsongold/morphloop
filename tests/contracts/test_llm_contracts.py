"""Contract tests for contracts/schemas/llm (ADR-0013, ADR-0014, ADR-0016).

Validates the structured LLM output schemas, their provider-compatibility
conventions (see contracts/schemas/llm/README.md) and the example outputs under
tests/contracts/fixtures/llm/.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, validate

ID_BASE = "https://morphloop.dev/contracts/"
LLM_DIR = CONTRACTS_DIR / "schemas" / "llm"
LLM_SCHEMAS = sorted(LLM_DIR.rglob("*.json"))

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))


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
