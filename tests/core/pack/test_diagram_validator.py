"""Diagram artifact-spec rules (#34, #56).

The shape is the JSON Schema ``contracts/schemas/pack/v2/diagram-spec.json``
(ported from the retired v0.1 visualization schema), applied through
``artifact-spec.json`` when ``type`` is ``diagram``. Cross-references the schema
cannot express (declared actors, unique ids) are the ``diagram`` validator.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from harness.core.pack.v2 import PackV2ImportError, import_pack_v2

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"
DIAGRAM = "artifacts/dns-resolution-flow.json"

type Doc = dict[str, Any]


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    dest = tmp_path / "pack"
    shutil.copytree(SE_PACK, dest)
    return dest


def _edit(path: Path, edit: Callable[[Doc], object]) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _problems(path: Path) -> str:
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(path)
    return "\n".join(info.value.problems)


def _steps(d: Doc) -> list[Doc]:
    steps: list[Doc] = d["spec"]["diagram"]["steps"]
    return steps


def _reality(d: Doc) -> Doc:
    step = next(s for s in _steps(d) if "reality" in s)
    reality: Doc = step["reality"]
    return reality


def _observe(d: Doc) -> Doc:
    observe: Doc = _reality(d)["observe"][0]
    return observe


def _no_reality(d: Doc) -> None:
    for step in _steps(d):
        step.pop("reality", None)


def test_se_pack_diagram_is_valid() -> None:
    import_pack_v2(SE_PACK)


# (edit, JSON path of the schema error inside the artifact file)
SCHEMA_CASES: dict[str, tuple[Callable[[Doc], object], str]] = {
    "missing title": (lambda d: d["spec"].pop("title"), "$.spec"),
    "title too long": (lambda d: d["spec"].__setitem__("title", "x" * 201), "$.spec.title"),
    "one actor": (
        lambda d: d["spec"]["diagram"].__setitem__("actors", d["spec"]["diagram"]["actors"][:1]),
        "$.spec.diagram.actors",
    ),
    "actor id not a token": (
        lambda d: d["spec"]["diagram"]["actors"][0].__setitem__("id", "client actor"),
        "$.spec.diagram.actors[0].id",
    ),
    "no reality": (_no_reality, "$.spec.diagram.steps"),
    "step id not a token": (
        lambda d: _steps(d)[0].__setitem__("id", "bad step"),
        "$.spec.diagram.steps[0].id",
    ),
    "step from not a string": (
        lambda d: _steps(d)[0].__setitem__("from", {"a": 1}),
        "$.spec.diagram.steps[0].from",
    ),
    "step label too long": (
        lambda d: _steps(d)[0].__setitem__("label", "x" * 201),
        "$.spec.diagram.steps[0].label",
    ),
    "explanation not a string": (
        lambda d: _steps(d)[0].__setitem__("explanation", {"text": "x"}),
        "$.spec.diagram.steps[0].explanation",
    ),
    "mechanism too long": (
        lambda d: _reality(d).__setitem__("mechanism", "x" * 20001),
        ".reality.mechanism",
    ),
    "unknown reality key": (lambda d: _reality(d).__setitem__("artifact", []), ".reality"),
    "artifacts null": (lambda d: _reality(d).__setitem__("artifacts", None), ".reality.artifacts"),
    "artifacts string": (lambda d: _reality(d).__setitem__("artifacts", ""), ".reality.artifacts"),
    "locator too long": (
        lambda d: _reality(d).__setitem__(
            "artifacts", [{"kind": "file", "locator": "/" * 1025, "description": "x"}]
        ),
        ".reality.artifacts[0].locator",
    ),
    "argv string": (lambda d: _observe(d).__setitem__("argv", "dig"), ".observe[0].argv"),
    "argv0 whitespace": (
        lambda d: _observe(d).__setitem__("argv", ["curl -sv"]),
        ".observe[0].argv[0]",
    ),
    "argv too many": (lambda d: _observe(d).__setitem__("argv", ["x"] * 65), ".observe[0].argv"),
    "argv0 too long": (
        lambda d: _observe(d).__setitem__("argv", ["x" * 257]),
        ".observe[0].argv[0]",
    ),
    "argv arg too long": (
        lambda d: _observe(d).__setitem__("argv", ["dig", "x" * 4097]),
        ".observe[0].argv[1]",
    ),
    "missing purpose": (lambda d: _observe(d).pop("purpose"), ".observe[0]"),
    "look_for empty": (lambda d: _observe(d).__setitem__("look_for", ""), ".observe[0].look_for"),
    "look_for object": (
        lambda d: _observe(d).__setitem__("look_for", {"text": "x"}),
        ".observe[0].look_for",
    ),
    "empty environment_bindings": (
        lambda d: d["spec"].__setitem__("environment_bindings", {}),
        "$.spec.environment_bindings",
    ),
    "boolean environment binding": (
        lambda d: d["spec"]["environment_bindings"].__setitem__("verbose", True),
        "$.spec.environment_bindings.verbose",
    ),
}


@pytest.mark.parametrize("case", SCHEMA_CASES)
def test_schema_rejects(pack: Path, case: str) -> None:
    edit, where = SCHEMA_CASES[case]
    _edit(pack / DIAGRAM, edit)
    problems = [p for p in _problems(pack).splitlines() if p.startswith(f"{DIAGRAM}: $")]
    assert any(where in p.split(": ")[1] for p in problems), problems


def test_step_referencing_unknown_actor_is_rejected(pack: Path) -> None:
    _edit(pack / DIAGRAM, lambda d: _steps(d)[0].__setitem__("from", "ghost"))
    assert (
        f"[diagram] {DIAGRAM}.spec.diagram.steps[0].from: 'ghost' is not a declared actor"
        in _problems(pack)
    )


def test_duplicate_step_id_is_rejected(pack: Path) -> None:
    def edit(d: Doc) -> None:
        _steps(d)[1]["id"] = _steps(d)[0]["id"]

    _edit(pack / DIAGRAM, edit)
    step_id = json.loads((SE_PACK / DIAGRAM).read_text())["spec"]["diagram"]["steps"][0]["id"]
    assert (
        f"[diagram] {DIAGRAM}.spec.diagram.steps[1].id: duplicate step id {step_id!r}"
        in _problems(pack)
    )


def test_duplicate_actor_id_is_rejected(pack: Path) -> None:
    def edit(d: Doc) -> None:
        actors = d["spec"]["diagram"]["actors"]
        actors[1]["id"] = actors[0]["id"]

    _edit(pack / DIAGRAM, edit)
    assert "[diagram] " + DIAGRAM + ".spec.diagram.actors[1].id: duplicate actor id" in _problems(
        pack
    )
