"""Contract tests for the real subject packs under contents/<pack-id>/.

Applies the same validation as tests/contracts/test_pack_contracts.py applies to the
fixture pack: every file is reached through the manifest index and validated against
the schema for its kind, and the hash bindings (activity -> reference solution,
generation record -> template and outputs) must hold. It also checks the cross-file
references the Importer enforces and that can be checked without a domain adapter
registry: referenced Definitions exist, ids are unique per kind, visualization steps
name declared actors, adapter items belong to a declared adapter, and prompts and
output schemas resolve.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from harness.testing.contracts import CONTRACTS_DIR, validate

ID_BASE = "https://morphloop.dev/contracts/"
MANIFEST = "schemas/pack/manifest.json"

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
    "prompt": None,
}

CONTENTS_DIR = CONTRACTS_DIR.parent / "contents"
PACK_DIRS = sorted(p.parent for p in CONTENTS_DIR.glob("*/manifest.json"))


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _document_hash(doc: Any) -> str:
    # RFC 8785 canonical JSON; equivalent to this for pack data
    # (ASCII keys, no floats that differ between repr and ECMAScript formatting).
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _manifest(pack: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = _load(pack / "manifest.json")
    return manifest


def _indexed(pack: Path) -> list[tuple[str, str]]:
    return sorted((path, entry["kind"]) for path, entry in _manifest(pack)["files"].items())


def _docs(pack: Path, kind: str) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for rel, k in _indexed(pack):
        if k == kind:
            doc = _load(pack / rel)
            docs[doc["id"]] = doc
    return docs


def _indexed_cases() -> list[tuple[Path, str, str]]:
    return [(pack, rel, kind) for pack in PACK_DIRS for rel, kind in _indexed(pack)]


def _pack_id(pack: Path) -> str:
    return pack.name


pack_param = pytest.mark.parametrize("pack", PACK_DIRS, ids=_pack_id)


def test_contents_has_a_pack() -> None:
    assert PACK_DIRS, f"no pack manifest under {CONTENTS_DIR}"


@pack_param
def test_manifest_is_valid(pack: Path) -> None:
    validate(_manifest(pack), MANIFEST)


@pack_param
def test_pack_id_matches_directory(pack: Path) -> None:
    assert _manifest(pack)["pack_id"] == pack.name


@pytest.mark.parametrize(
    ("pack", "rel", "kind"),
    _indexed_cases(),
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_indexed_file_is_valid(pack: Path, rel: str, kind: str) -> None:
    path = pack / rel
    assert path.is_file(), f"indexed file missing: {rel}"
    schema = KIND_SCHEMA[kind]
    if schema is not None:
        validate(_load(path), schema)
    else:
        assert path.read_text(encoding="utf-8").strip(), f"empty prompt: {rel}"


@pack_param
def test_every_pack_file_is_indexed(pack: Path) -> None:
    on_disk = {
        p.relative_to(pack).as_posix()
        for p in pack.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert on_disk == set(_manifest(pack)["files"])


@pack_param
def test_ids_are_unique_per_kind(pack: Path) -> None:
    seen: dict[str, set[str]] = {}
    for rel, kind in _indexed(pack):
        if KIND_SCHEMA[kind] is None or kind in {"layout", "generation_record"}:
            continue
        doc_id = _load(pack / rel)["id"]
        assert doc_id not in seen.setdefault(kind, set()), f"duplicate {kind} id {doc_id}"
        seen[kind].add(doc_id)


@pack_param
def test_reference_solutions_are_bound_by_hash_and_separate(pack: Path) -> None:
    files = _manifest(pack)["files"]
    for activity in _docs(pack, "activity").values():
        ref = activity.get("reference_solution")
        if ref is None:
            continue
        solution = _load(pack / ref["path"])
        assert files[ref["path"]]["kind"] == "reference_solution"
        assert ref["content_hash"] == _document_hash(solution)
        assert solution["activity_id"] == activity["id"]


@pack_param
def test_generation_record_hashes_match_outputs_and_template(pack: Path) -> None:
    files = _manifest(pack)["files"]
    templates = _docs(pack, "activity_template")
    for rel, kind in _indexed(pack):
        if kind != "generation_record":
            continue
        record = _load(pack / rel)
        template = templates[record["template"]["template_id"]]
        assert record["template"]["template_hash"] == _document_hash(template)
        for output in record["outputs"]:
            doc = _load(pack / output["path"])
            assert doc["id"] == output["definition_id"]
            assert output["definition_hash"] == _document_hash(doc)
            assert files[output["path"]]["kind"] == output["kind"]
            if output["kind"] == "activity":
                assert doc["origin"] == {
                    "type": "generated",
                    "template_id": template["id"],
                    "timing": record["timing"],
                }


@pack_param
def test_generated_activities_have_a_generation_record(pack: Path) -> None:
    recorded = {
        output["definition_id"]
        for rel, kind in _indexed(pack)
        if kind == "generation_record"
        for output in _load(pack / rel)["outputs"]
    }
    for activity in _docs(pack, "activity").values():
        if activity["origin"]["type"] == "generated":
            assert activity["id"] in recorded, activity["id"]


@pack_param
def test_v01_generation_timing_is_authoring(pack: Path) -> None:
    manifest = _manifest(pack)
    assert manifest["content_generation"]["default_timing"] == "authoring"
    for template in _docs(pack, "activity_template").values():
        assert template.get("timing", "authoring") == "authoring"


@pack_param
def test_referenced_definitions_exist(pack: Path) -> None:
    skills = set(_docs(pack, "skill"))
    evaluators = set(_docs(pack, "evaluator"))
    environments = _docs(pack, "environment")
    visualizations = set(_docs(pack, "visualization"))
    references = set(_docs(pack, "reference"))

    def check_targets(doc: dict[str, Any]) -> None:
        targets = doc["skills"]
        for skill in targets["primary"] + targets.get("secondary", []):
            assert skill in skills, f"{doc['id']}: unknown skill {skill}"
        assert doc["evaluator"] in evaluators, f"{doc['id']}: unknown evaluator"
        remediation = doc.get("remediation", {})
        for ref in remediation.get("references", []):
            assert ref in references, f"{doc['id']}: unknown reference {ref}"
        for viz in remediation.get("visualizations", []):
            assert viz in visualizations, f"{doc['id']}: unknown visualization {viz}"

    for activity in _docs(pack, "activity").values():
        check_targets(activity)
        env = activity.get("environment")
        if env is not None:
            assert env in environments, f"{activity['id']}: unknown environment {env}"
    for template in _docs(pack, "activity_template").values():
        check_targets(template)
    for skill in _docs(pack, "skill").values():
        for prereq in skill["prerequisites"]:
            assert prereq in skills, f"{skill['id']}: unknown prerequisite {prereq}"
    for kind in ("reference", "visualization"):
        for doc in _docs(pack, kind).values():
            for skill in doc.get("skills", []):
                assert skill in skills, f"{doc['id']}: unknown skill {skill}"
    for rel, kind in _indexed(pack):
        if kind == "layout":
            for chapter in _load(pack / rel).get("toc", {}).get("chapters", []):
                for item in chapter["items"]:
                    assert item["id"] in _docs(pack, item["kind"]), f"{rel}: unknown toc {item}"


@pack_param
def test_evaluator_dimensions_come_from_skill_vocabulary(pack: Path) -> None:
    vocabulary = {d for s in _docs(pack, "skill").values() for d in s["evidence_dimensions"]}
    for evaluator in _docs(pack, "evaluator").values():
        for dim in evaluator.get("semantic", {}).get("dimensions", []):
            assert dim["id"] in vocabulary, f"{evaluator['id']}: dimension {dim['id']}"


@pack_param
def test_visualization_steps_name_declared_actors(pack: Path) -> None:
    for viz in _docs(pack, "visualization").values():
        actors = [a["id"] for a in viz["diagram"]["actors"]]
        assert len(actors) == len(set(actors)), f"{viz['id']}: duplicate actor"
        steps = [s["id"] for s in viz["diagram"]["steps"]]
        assert len(steps) == len(set(steps)), f"{viz['id']}: duplicate step"
        for step in viz["diagram"]["steps"]:
            assert step["from"] in actors, f"{viz['id']}/{step['id']}: from {step['from']}"
            assert step["to"] in actors, f"{viz['id']}/{step['id']}: to {step['to']}"


@pack_param
def test_adapter_items_belong_to_declared_adapters(pack: Path) -> None:
    adapters = set(_manifest(pack)["domain_adapters"])
    items: list[str] = []
    for activity in _docs(pack, "activity").values():
        items += activity["tools"] + [c["check"] for c in activity["checks"]]
    for env in _docs(pack, "environment").values():
        items.append(env["fixture"])
    for template in _docs(pack, "activity_template").values():
        items += template["tools"] + template["allowed_fixtures"] + template["allowed_checks"]
    for item in items:
        assert item.split(".", 1)[0] in adapters, f"{item}: adapter not declared"


@pack_param
def test_generated_activities_stay_within_template_bounds(pack: Path) -> None:
    templates = _docs(pack, "activity_template")
    environments = _docs(pack, "environment")
    for activity in _docs(pack, "activity").values():
        origin = activity["origin"]
        if origin["type"] != "generated":
            continue
        template = templates[origin["template_id"]]
        assert {c["check"] for c in activity["checks"]} <= set(template["allowed_checks"])
        assert activity["tools"] == template["tools"]
        assert activity["skills"] == template["skills"]
        assert activity["difficulty"] == template["difficulty"]
        assert activity["evaluator"] == template["evaluator"]
        env_id = activity.get("environment")
        if env_id is not None:
            env = environments[env_id]
            assert env["fixture"] in template["allowed_fixtures"]
            assert env["image"] == template["image"]


@pack_param
def test_registry_prompts_resolve_to_indexed_prompt_files(pack: Path) -> None:
    manifest = _manifest(pack)
    prompts = {
        (e["prompt_id"], e["prompt_version"])
        for e in manifest["files"].values()
        if e["kind"] == "prompt"
    }
    for selection in manifest["registry"].values():
        llm = selection["llm"]
        assert (llm["prompt_id"], llm["prompt_version"]) in prompts


@pack_param
def test_generation_record_generator_matches_registry(pack: Path) -> None:
    generator = _manifest(pack)["registry"].get("generator")
    for rel, kind in _indexed(pack):
        if kind == "generation_record":
            assert generator is not None
            record = _load(pack / rel)["generator"]
            assert record["implementation"] == generator["implementation"]
            assert record["llm"] == generator["llm"]


@pack_param
def test_llm_output_schemas_exist(pack: Path) -> None:
    for selection in _manifest(pack)["registry"].values():
        ref = selection.get("output_schema")
        if ref is None:
            continue
        assert (CONTRACTS_DIR / ref.removeprefix(ID_BASE)).is_file(), ref


@pack_param
def test_visualizations_target_the_activity_lab(pack: Path) -> None:
    """Every host, port and URL a named visualization shows is the activity's lab's (AC-C2/C3)."""
    envs = _docs(pack, "environment")
    vizzes = _docs(pack, "visualization")
    for activity in _docs(pack, "activity").values():
        params = envs[activity["environment"]]["params"] if "environment" in activity else {}
        for viz_id in activity.get("remediation", {}).get("visualizations", []):
            viz = vizzes[viz_id]
            where = f"{activity['id']} -> {viz_id}"
            for name, value in viz.get("environment_bindings", {}).items():
                assert params.get(name) == value, f"{where}: {name}"
            if "zone" not in params:
                continue
            text = json.dumps(viz)
            service, port = params["service_name"], params["service_port"]
            hosts = set(
                re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\." + re.escape(params["zone"]), text)
            )
            assert hosts == {service}, f"{where}: hosts {hosts}"
            ports = set(re.findall(r"(?:>|" + re.escape(service) + r"):(\d+)", text))
            assert ports == {str(port)}, f"{where}: ports {ports}"
            argvs = [
                arg
                for step in viz["diagram"]["steps"]
                for obs in step.get("reality", {}).get("observe", [])
                for arg in obs["argv"]
            ]
            urls = {a for a in argvs if "://" in a}
            assert urls == {f"http://{service}:{port}{params['health_path']}"}, f"{where}: {urls}"
