"""Diagram artifact-spec structural rules (#34, #56).

A ``type: "diagram"`` artifact-spec's ``spec`` shape is not fixed by the JSON
Schema (``contracts/schemas/pack/v2/artifact-spec.json`` only ``$ref``s a fixed
shape for ``lab``), so this module enforces the same shape the retired v0.1
visualization schema did (``contracts/schemas/pack/visualization.json``): a
sequence diagram whose steps reference declared actors, where at least one
step carries a reality mapping (a mechanism plus at least one runnable
observation).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from harness.core.ports.lab_runtime import check_argv

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


# common/ids.json#/$defs/token, as the retired visualization schema used for step ids.
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _text(value: object) -> str | None:
    """``value`` if it is a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None


def _check_observe(where: str, observe: object) -> list[str]:
    if not isinstance(observe, Sequence) or not observe:
        return [f"{where}: must be a non-empty array"]
    problems: list[str] = []
    for i, item in enumerate(observe):
        item_where = f"{where}[{i}]"
        if not isinstance(item, Mapping):
            problems.append(f"{item_where}: must be an object")
            continue
        argv = item.get("argv")
        if isinstance(argv, str) or not isinstance(argv, Sequence):
            problems.append(f"{item_where}.argv: must be an array")
        else:
            try:
                check_argv(tuple(argv))  # pack/defs.json#/$defs/sandbox_argv
            except ValueError as exc:
                problems.append(f"{item_where}.argv: {exc}")
        if _text(item.get("purpose")) is None:
            problems.append(f"{item_where}.purpose: is required")
        if "look_for" in item and _text(item["look_for"]) is None:
            problems.append(f"{item_where}.look_for: must be a non-empty string")
    return problems


def _check_reality(where: str, reality: object) -> list[str]:
    if not isinstance(reality, Mapping):
        return [f"{where}: must be an object"]
    problems: list[str] = []
    if _text(reality.get("mechanism")) is None:
        problems.append(f"{where}.mechanism: is required")
    problems += _check_observe(f"{where}.observe", reality.get("observe"))
    artifacts = reality.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, Sequence):
            problems.append(f"{where}.artifacts: must be an array")
        else:
            for i, art in enumerate(artifacts):
                if not isinstance(art, Mapping) or any(
                    _text(art.get(k)) is None for k in ("kind", "locator", "description")
                ):
                    problems.append(f"{where}.artifacts[{i}]: needs kind, locator and description")
    return problems


def _actor_ids(actors: object) -> frozenset[str]:
    """Ids of the actors that at least declared a non-empty id and label."""
    if not isinstance(actors, Sequence):
        return frozenset()
    ids: list[str] = []
    for actor in actors:
        if isinstance(actor, Mapping):
            actor_id = _text(actor.get("id"))
            if actor_id is not None and _text(actor.get("label")) is not None:
                ids.append(actor_id)
    return frozenset(ids)


def _check_actors(where: str, actors: object) -> list[str]:
    if not isinstance(actors, Sequence) or len(actors) < 2:
        return [f"{where}: must have at least 2 actors"]
    problems: list[str] = []
    seen: set[str] = set()
    for i, actor in enumerate(actors):
        actor_where = f"{where}[{i}]"
        if not isinstance(actor, Mapping):
            problems.append(f"{actor_where}: must be an object")
            continue
        actor_id = _text(actor.get("id"))
        label = _text(actor.get("label"))
        if actor_id is None or label is None:
            problems.append(f"{actor_where}: needs a non-empty id and label")
        elif actor_id in seen:
            problems.append(f"{actor_where}: duplicate actor id {actor_id!r}")
        else:
            seen.add(actor_id)
    return problems


def _check_steps(where: str, steps: object, actor_ids: frozenset[str]) -> list[str]:
    if not isinstance(steps, Sequence) or not steps:
        return [f"{where}: must have at least 1 step"]
    problems: list[str] = []
    seen_ids: set[str] = set()
    has_reality = False
    for i, step in enumerate(steps):
        step_where = f"{where}[{i}]"
        if not isinstance(step, Mapping):
            problems.append(f"{step_where}: must be an object")
            continue
        step_id = _text(step.get("id"))
        if step_id is None:
            problems.append(f"{step_where}.id: is required")
        elif not _TOKEN_RE.fullmatch(step_id):
            problems.append(f"{step_where}.id: must be a token")
        elif step_id in seen_ids:
            problems.append(f"{step_where}.id: duplicate step id {step_id!r}")
        else:
            seen_ids.add(step_id)
        if _text(step.get("label")) is None:
            problems.append(f"{step_where}.label: is required")
        for end in ("from", "to"):
            if not isinstance(step.get(end), str):
                problems.append(f"{step_where}.{end}: must be a string")
            elif step.get(end) not in actor_ids:
                problems.append(f"{step_where}.{end}: {step.get(end)!r} is not a declared actor")
        if "reality" in step:
            has_reality = True
            problems += _check_reality(f"{step_where}.reality", step["reality"])
    if not has_reality:
        problems.append(f"{where}: at least one step must carry a reality mapping")
    return problems


def _check_diagram(where: str, diagram: object) -> list[str]:
    if not isinstance(diagram, Mapping):
        return [f"{where}: must be an object"]
    problems: list[str] = []
    if diagram.get("type") != "sequence":
        problems.append(f"{where}.type: must be 'sequence'")
    problems += _check_actors(f"{where}.actors", diagram.get("actors"))
    actor_ids = _actor_ids(diagram.get("actors"))
    problems += _check_steps(f"{where}.steps", diagram.get("steps"), actor_ids)
    return problems


def _check_environment_bindings(where: str, bindings: object) -> list[str]:
    if not isinstance(bindings, Mapping) or not bindings:
        return [f"{where}: must be a non-empty object"]
    problems: list[str] = []
    for key, value in bindings.items():
        if isinstance(value, bool) or not isinstance(value, str | int) or value == "":
            problems.append(f"{where}[{key!r}]: must be a non-empty string or an integer")
    return problems


def validate(pack: PackV2) -> list[str]:
    problems: list[str] = []
    for path, doc in pack.documents.get("artifacts", {}).items():
        if doc.get("type") != "diagram":
            continue
        spec = doc.get("spec")
        if not isinstance(spec, Mapping):
            problems.append(f"{path}: spec must be an object")
            continue
        if _text(spec.get("title")) is None:
            problems.append(f"{path}.spec.title: is required")
        problems += _check_diagram(f"{path}.spec.diagram", spec.get("diagram"))
        if "environment_bindings" in spec:
            problems += _check_environment_bindings(
                f"{path}.spec.environment_bindings", spec["environment_bindings"]
            )
    return problems
