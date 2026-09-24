"""Contract tests for contracts/schemas/{common,events} (ADR-0008, ADR-0016).

Validates the schemas themselves, the $id / file layout convention, and example
instances under tests/contracts/fixtures/events/ against the real contracts tree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, validate

ID_BASE = "https://morphloop.dev/contracts/"
SCHEMA_DIRS = [CONTRACTS_DIR / "schemas" / "common", CONTRACTS_DIR / "schemas" / "events"]
PAYLOADS_DIR = CONTRACTS_DIR / "schemas" / "events" / "payloads"
STORED = "schemas/events/envelope/stored.json"
APPEND = "schemas/events/envelope/append.json"

FIXTURES = Path(__file__).parent / "fixtures" / "events"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))
DB_ASSIGNED = ("position", "recorded_at")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_files() -> list[Path]:
    return sorted(p for d in SCHEMA_DIRS for p in d.rglob("*.json"))


def _payload_versions() -> set[tuple[str, int]]:
    # v1 only: v2 payloads ("x-envelope": 2) are dispatched by contract_schemas (#43).
    return {
        (p.parent.name, int(p.stem))
        for p in PAYLOADS_DIR.glob("*/*.json")
        if "x-envelope" not in _load(p)
    }


def _payload_path(event: dict[str, Any]) -> str:
    return f"schemas/events/payloads/{event['event_type']}/{event['event_version']}.json"


def _to_append_request(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in DB_ASSIGNED}


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: str(p.relative_to(CONTRACTS_DIR)))
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: str(p.relative_to(CONTRACTS_DIR)))
def test_schema_id_matches_file_path(path: Path) -> None:
    # Convention: $id == ID_BASE + path relative to contracts/.
    assert _load(path)["$id"] == ID_BASE + path.relative_to(CONTRACTS_DIR).as_posix()


def test_payload_layout_is_type_slash_major_version() -> None:
    files = sorted(PAYLOADS_DIR.rglob("*.json"))
    assert files
    for path in files:
        rel = path.relative_to(PAYLOADS_DIR)
        assert len(rel.parts) == 2, rel
        assert re.fullmatch(r"[a-z][a-z_]*(\.[a-z][a-z_]*)+", rel.parts[0]), rel
        assert re.fullmatch(r"[1-9][0-9]*", path.stem), rel


def test_dispatch_covers_exactly_the_payload_schemas() -> None:
    dispatch = _load(CONTRACTS_DIR / "schemas" / "events" / "dispatch.json")
    registered: set[tuple[str, int]] = set()
    for branch in dispatch["allOf"]:
        etype = branch["if"]["properties"]["event_type"]["const"]
        for version in branch["then"]["properties"]["event_version"]["enum"]:
            registered.add((etype, version))
    assert registered == _payload_versions()
    assert set(dispatch["properties"]["event_type"]["enum"]) == {t for t, _ in registered}


def test_every_event_type_version_has_a_valid_example() -> None:
    examples = {(e["event_type"], e["event_version"]) for e in map(_load, VALID)}
    assert examples == _payload_versions()


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_stored_event(path: Path) -> None:
    event = _load(path)
    assert path.stem == f"{event['event_type']}.v{event['event_version']}"
    validate(event, STORED)
    validate(event["payload"], _payload_path(event))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_append_request(path: Path) -> None:
    validate(_to_append_request(_load(path)), APPEND)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_event_is_rejected(path: Path) -> None:
    case = _load(path)
    with pytest.raises(ContractViolation) as excinfo:
        validate(case["instance"], case["schema"])
    reported = {
        line.split(": ", 1)[0] for line in str(excinfo.value).splitlines()[1:] if ": " in line
    }
    for expected in case["expected_error_paths"]:
        assert expected in reported, f"{expected} not in {sorted(reported)}"


def test_causation_chain_is_traceable_in_examples() -> None:
    # evaluation.completed -> evidence.created -> learner_skill.updated (ADR-0008, AC-F3).
    by_type = {e["event_type"]: e for e in map(_load, VALID)}
    update = by_type["learner_skill.updated"]
    evidence = by_type["evidence.created"]
    evaluation = by_type["evaluation.completed"]
    assert update["causation_id"] == evidence["event_id"]
    assert evidence["payload"]["evidence_id"] in update["payload"]["evidence_ids"]
    assert evidence["causation_id"] == evaluation["event_id"]
    assert evidence["payload"]["evaluation_id"] == evaluation["payload"]["evaluation_id"]


def test_highlight_thread_and_memo_ids_derive_from_the_highlight() -> None:
    # One popup thread and one memo per highlight: 'thr_' / 'memo_' + the highlight_id tail.
    by_type = {(e["event_type"], e["event_version"]): e for e in map(_load, VALID)}
    highlight = by_type[("content.highlighted", 2)]
    recorded = by_type[("memo.recorded", 1)]["payload"]
    edited = by_type[("memo.edited", 1)]["payload"]
    tail = highlight["payload"]["highlight_id"].removeprefix("hl_")
    assert recorded["highlight_id"] == highlight["payload"]["highlight_id"]
    assert recorded["thread_id"] == f"thr_{tail}"
    assert recorded["memo_id"] == edited["memo_id"] == f"memo_{tail}"
    assert recorded["source_event_ids"][0] == highlight["event_id"]
