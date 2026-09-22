"""Contract tests for contracts/schemas/ws (ADR-0008, ADR-0015, ADR-0016, ADR-0017).

Validates the schemas themselves, the $id / file layout convention, and example
messages under tests/contracts/fixtures/ws/ against the real contracts tree.
Mirrors the style of tests/contracts/test_event_contracts.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, validate

ID_BASE = "https://morphloop.dev/contracts/"
SCHEMA_DIR = CONTRACTS_DIR / "schemas" / "ws"
PAYLOADS_DIR = SCHEMA_DIR / "payloads"
MESSAGE = "schemas/ws/envelope/message.json"

FIXTURES = Path(__file__).parent / "fixtures" / "ws"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_files() -> list[Path]:
    return sorted(SCHEMA_DIR.rglob("*.json"))


def _payload_types() -> set[str]:
    return {p.stem for p in PAYLOADS_DIR.glob("*.json")}


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: str(p.relative_to(CONTRACTS_DIR)))
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: str(p.relative_to(CONTRACTS_DIR)))
def test_schema_id_matches_file_path(path: Path) -> None:
    # Convention: $id == ID_BASE + path relative to contracts/ (contracts/README.md).
    assert _load(path)["$id"] == ID_BASE + path.relative_to(CONTRACTS_DIR).as_posix()


def test_dispatch_covers_exactly_the_payload_schemas() -> None:
    dispatch = _load(SCHEMA_DIR / "dispatch.json")
    registered = {branch["if"]["properties"]["type"]["const"] for branch in dispatch["allOf"]}
    assert registered == _payload_types()
    assert set(dispatch["properties"]["type"]["enum"]) == registered


def test_every_message_type_has_a_valid_example() -> None:
    examples = {_load(p)["type"] for p in VALID}
    assert examples == _payload_types()


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_message(path: Path) -> None:
    message = _load(path)
    assert path.stem == message["type"]
    validate(message, MESSAGE)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_message_is_rejected(path: Path) -> None:
    case = _load(path)
    with pytest.raises(ContractViolation) as excinfo:
        validate(case["instance"], case["schema"])
    reported = {
        line.split(": ", 1)[0] for line in str(excinfo.value).splitlines()[1:] if ": " in line
    }
    for expected in case["expected_error_paths"]:
        assert expected in reported, f"{expected} not in {sorted(reported)}"
