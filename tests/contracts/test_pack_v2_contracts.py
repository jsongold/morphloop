"""Contract tests for contracts/schemas/pack/v2 (pack v2 files).

Checks the schemas themselves and the $id convention, validates the example pack
under tests/contracts/fixtures/pack-v2/valid/dns-pack/ through its manifest, and
checks that the cases in tests/contracts/fixtures/pack-v2/invalid/ fail at the
expected paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, validate

ID_BASE = "https://morphloop.dev/contracts/"
V2 = "schemas/pack/v2/"
SCHEMA_FILES = sorted((CONTRACTS_DIR / V2).glob("*.json"))
FIXTURES = Path(__file__).parent / "fixtures" / "pack-v2"
PACK_DIR = FIXTURES / "valid" / "dns-pack"
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

LIST_SCHEMA = {
    "topics": "topic.json",
    "textbooks": "textbook-doc.json",
    "drills": "drill-item.json",
    "artifacts": "artifact-spec.json",
    "llm_roles": "llm-role.json",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = _load(PACK_DIR / "manifest.json")
    return manifest


def _listed() -> list[tuple[str, str]]:
    manifest = _manifest()
    pairs = [(manifest["labels"], "labels.json")]
    pairs += [(p, schema) for key, schema in LIST_SCHEMA.items() for p in manifest[key]]
    return pairs


def _prompt_paths() -> set[str]:
    """Markdown prompt paths referenced from llm_roles config files.

    Not schema-validated JSON, so not part of ``_listed()``, but still a real
    pack file the "every file is listed" check must account for.
    """
    return {_load(PACK_DIR / p)["prompt"] for p in _manifest()["llm_roles"]}


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12_with_id(path: Path) -> None:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == ID_BASE + path.relative_to(CONTRACTS_DIR).as_posix()


def test_valid_manifest() -> None:
    validate(_manifest(), V2 + "manifest.json")


@pytest.mark.parametrize(("rel", "schema"), _listed(), ids=lambda v: str(v))
def test_valid_listed_file(rel: str, schema: str) -> None:
    validate(_load(PACK_DIR / rel), V2 + schema)


def test_every_pack_file_is_listed() -> None:
    on_disk = {
        p.relative_to(PACK_DIR).as_posix()
        for p in PACK_DIR.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert on_disk == {rel for rel, _ in _listed()} | _prompt_paths()


def test_every_answer_mode_has_a_valid_example() -> None:
    modes = {_load(PACK_DIR / p)["answer_mode"] for p in _manifest()["drills"]}
    enum = _load(CONTRACTS_DIR / V2 / "drill-item.json")["properties"]["answer_mode"]["enum"]
    assert modes == set(enum)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_pack_v2_document_is_rejected(path: Path) -> None:
    case = _load(path)
    with pytest.raises(ContractViolation) as excinfo:
        validate(case["instance"], case["schema"])
    reported = {
        line.split(": ", 1)[0] for line in str(excinfo.value).splitlines()[1:] if ": " in line
    }
    for expected in case["expected_error_paths"]:
        assert expected in reported, f"{expected} not in {sorted(reported)}"
