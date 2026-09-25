"""Diagram artifact-spec structural rules (#34, #56): equivalent to the retired
v0.1 visualization schema, enforced as a pack v2 cross-file validator since the
JSON Schema leaves a diagram's `spec` shape open (only `lab` is `$ref`-fixed).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from harness.core.pack.v2 import PackV2ImportError, import_pack_v2

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"
DIAGRAM = "artifacts/dns-resolution-flow.json"


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    dest = tmp_path / "pack"
    shutil.copytree(SE_PACK, dest)
    return dest


def _edit(path: Path, edit: Any) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _problems(path: Path) -> str:
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(path)
    return "\n".join(info.value.problems)


def test_se_pack_diagram_has_no_diagram_problems() -> None:
    import_pack_v2(SE_PACK)  # raises PackV2ImportError if any [diagram] problem is found


def test_missing_title_is_rejected(pack: Path) -> None:
    _edit(pack / DIAGRAM, lambda d: d["spec"].pop("title"))
    assert f"[diagram] {DIAGRAM}.spec.title: is required" in _problems(pack)


def test_fewer_than_two_actors_is_rejected(pack: Path) -> None:
    _edit(
        pack / DIAGRAM,
        lambda d: d["spec"]["diagram"].__setitem__("actors", [d["spec"]["diagram"]["actors"][0]]),
    )
    assert f"[diagram] {DIAGRAM}.spec.diagram.actors: must have at least 2 actors" in _problems(
        pack
    )


def test_step_referencing_unknown_actor_is_rejected(pack: Path) -> None:
    _edit(pack / DIAGRAM, lambda d: d["spec"]["diagram"]["steps"][0].__setitem__("from", "ghost"))
    problems = _problems(pack)
    assert (
        f"[diagram] {DIAGRAM}.spec.diagram.steps[0].from: 'ghost' is not a declared actor"
        in problems
    )


def test_no_step_with_reality_is_rejected(pack: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        for step in d["spec"]["diagram"]["steps"]:
            step.pop("reality", None)

    _edit(pack / DIAGRAM, edit)
    problems = _problems(pack)
    assert (
        f"[diagram] {DIAGRAM}.spec.diagram.steps: at least one step must carry a reality mapping"
        in problems
    )


def test_duplicate_step_id_is_rejected(pack: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        steps = d["spec"]["diagram"]["steps"]
        steps[1]["id"] = steps[0]["id"]

    _edit(pack / DIAGRAM, edit)
    step_id = json.loads((SE_PACK / DIAGRAM).read_text())["spec"]["diagram"]["steps"][0]["id"]
    assert (
        f"[diagram] {DIAGRAM}.spec.diagram.steps[1].id: duplicate step id {step_id!r}"
        in _problems(pack)
    )


def test_observe_missing_purpose_is_rejected(pack: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        for step in d["spec"]["diagram"]["steps"]:
            if "reality" in step:
                del step["reality"]["observe"][0]["purpose"]
                break

    _edit(pack / DIAGRAM, edit)
    assert "reality.observe[0].purpose: is required" in _problems(pack)


def test_argv_with_whitespace_first_element_is_rejected(pack: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        for step in d["spec"]["diagram"]["steps"]:
            if "reality" in step:
                step["reality"]["observe"][0]["argv"][0] = "curl -sv"
                break

    _edit(pack / DIAGRAM, edit)
    assert "argv[0]: must not contain whitespace" in _problems(pack)


def test_empty_environment_bindings_is_rejected(pack: Path) -> None:
    _edit(pack / DIAGRAM, lambda d: d["spec"].__setitem__("environment_bindings", {}))
    assert (
        f"[diagram] {DIAGRAM}.spec.environment_bindings: must be a non-empty object"
        in _problems(pack)
    )


def test_boolean_environment_binding_value_is_rejected(pack: Path) -> None:
    _edit(
        pack / DIAGRAM,
        lambda d: d["spec"]["environment_bindings"].__setitem__("verbose", True),
    )
    assert "environment_bindings['verbose']: must be a non-empty string or an integer" in _problems(
        pack
    )
