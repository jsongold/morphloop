"""Contract tests for contracts/schemas/pack (subject pack files).

Validates the schemas themselves, the $id convention, a realistic DNS pack under
tests/contracts/fixtures/pack/valid/dns-pack/ (every file reached through the manifest
file index), the hash bindings between documents, and invalid cases under
tests/contracts/fixtures/pack/invalid/.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, validate

ID_BASE = "https://morphloop.dev/contracts/"
PACK_SCHEMAS = CONTRACTS_DIR / "schemas" / "pack"
MANIFEST = "schemas/pack/manifest.json"
DEFS = "defs.json"

KIND_SCHEMA: dict[str, str | None] = {
    "skill": "schemas/pack/skill.json",
    "activity": "schemas/pack/activity-definition.json",
    "reference_solution": "schemas/pack/reference-solution.json",
    "environment": "schemas/pack/environment.json",
    "activity_template": "schemas/pack/activity-template.json",
    "generation_record": "schemas/pack/generation-record.json",
    "evaluator": "schemas/pack/evaluator.json",
    "visualization": "schemas/pack/visualization.json",
    "reference": "schemas/pack/reference.json",
    "layout": "schemas/pack/layout.json",
    "prompt": None,  # plain text, identified by prompt_id + prompt_version in the index
}

FIXTURES = Path(__file__).parent / "fixtures" / "pack"
PACK_DIR = FIXTURES / "valid" / "dns-pack"
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _document_hash(doc: Any) -> str:
    # RFC 8785 canonical JSON; equivalent to this for the fixture data
    # (ASCII keys, no floats that differ between repr and ECMAScript formatting).
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_files() -> list[Path]:
    return sorted(PACK_SCHEMAS.rglob("*.json"))


def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = _load(PACK_DIR / "manifest.json")
    return manifest


def _indexed() -> list[tuple[str, str]]:
    return sorted((path, entry["kind"]) for path, entry in _manifest()["files"].items())


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: p.name)
def test_schema_id_matches_file_path(path: Path) -> None:
    assert _load(path)["$id"] == ID_BASE + path.relative_to(CONTRACTS_DIR).as_posix()


def test_kind_table_matches_manifest_enum_and_schema_files() -> None:
    enum = _load(PACK_SCHEMAS / "manifest.json")["$defs"]["file_entry"]["properties"]["kind"]
    assert set(enum["enum"]) == set(KIND_SCHEMA)
    mapped = {s for s in KIND_SCHEMA.values() if s is not None} | {MANIFEST}
    on_disk = {p.relative_to(CONTRACTS_DIR).as_posix() for p in _schema_files() if p.name != DEFS}
    assert mapped == on_disk


def test_valid_manifest() -> None:
    validate(_manifest(), MANIFEST)


@pytest.mark.parametrize(("rel", "kind"), _indexed(), ids=lambda v: str(v))
def test_valid_indexed_file(rel: str, kind: str) -> None:
    path = PACK_DIR / rel
    assert path.is_file(), f"indexed file missing: {rel}"
    schema = KIND_SCHEMA[kind]
    if schema is not None:
        validate(_load(path), schema)


def test_every_pack_file_is_indexed() -> None:
    on_disk = {
        p.relative_to(PACK_DIR).as_posix()
        for p in PACK_DIR.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert on_disk == set(_manifest()["files"])


def test_every_kind_has_a_valid_example() -> None:
    assert {kind for _, kind in _indexed()} == set(KIND_SCHEMA)


def test_reference_solutions_are_bound_by_hash_and_separate() -> None:
    for rel, kind in _indexed():
        if kind != "activity":
            continue
        activity = _load(PACK_DIR / rel)
        ref = activity.get("reference_solution")
        if ref is None:
            continue
        solution = _load(PACK_DIR / ref["path"])
        assert _manifest()["files"][ref["path"]]["kind"] == "reference_solution"
        assert ref["content_hash"] == _document_hash(solution)
        assert solution["activity_id"] == activity["id"]


def test_generation_record_hashes_match_outputs_and_template() -> None:
    records = [rel for rel, kind in _indexed() if kind == "generation_record"]
    assert records
    templates = {
        _load(PACK_DIR / rel)["id"]: _load(PACK_DIR / rel)
        for rel, kind in _indexed()
        if kind == "activity_template"
    }
    for rel in records:
        record = _load(PACK_DIR / rel)
        template = templates[record["template"]["template_id"]]
        assert record["template"]["template_hash"] == _document_hash(template)
        for output in record["outputs"]:
            doc = _load(PACK_DIR / output["path"])
            assert doc["id"] == output["definition_id"]
            assert output["definition_hash"] == _document_hash(doc)
            assert _manifest()["files"][output["path"]]["kind"] == output["kind"]
            if output["kind"] == "activity":
                assert doc["origin"] == {
                    "type": "generated",
                    "template_id": template["id"],
                    "timing": record["timing"],
                }


def test_registry_prompts_resolve_to_indexed_prompt_files() -> None:
    manifest = _manifest()
    prompts = {
        (e["prompt_id"], e["prompt_version"])
        for e in manifest["files"].values()
        if e["kind"] == "prompt"
    }
    for selection in manifest["registry"].values():
        llm = selection["llm"]
        assert (llm["prompt_id"], llm["prompt_version"]) in prompts


def test_llm_output_schemas_exist() -> None:
    for selection in _manifest()["registry"].values():
        ref = selection.get("output_schema")
        if ref is None:
            continue
        assert (CONTRACTS_DIR / ref.removeprefix(ID_BASE)).is_file(), ref


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_pack_document_is_rejected(path: Path) -> None:
    case = _load(path)
    with pytest.raises(ContractViolation) as excinfo:
        validate(case["instance"], case["schema"])
    reported = {
        line.split(": ", 1)[0] for line in str(excinfo.value).splitlines()[1:] if ": " in line
    }
    for expected in case["expected_error_paths"]:
        assert expected in reported, f"{expected} not in {sorted(reported)}"


def test_layout_memo_region_exists_and_has_a_summarizer() -> None:
    # Importer rules for the layout `memo` key (contracts/schemas/pack/layout.json).
    layout = _load(PACK_DIR / "ux" / "layout.json")
    assert layout["memo"]["region"] in layout["layout"]
    assert "memo_summarizer" in _manifest()["registry"]
