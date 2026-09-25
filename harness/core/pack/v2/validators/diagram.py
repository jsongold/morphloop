"""Diagram artifact-spec cross-references (#34, #56).

A ``type: "diagram"`` artifact-spec's ``spec`` shape is the JSON Schema
``contracts/schemas/pack/v2/diagram-spec.json``, applied by the Importer's
schema pass through ``artifact-spec.json``. Validators run only after every
file passed its schema, so this module checks just what a schema cannot:
actor and step ids are unique, and every step's ``from`` / ``to`` names a
declared actor.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


def _items(value: object) -> Iterator[tuple[int, Mapping[str, object]]]:
    assert isinstance(value, Sequence)  # schema-valid
    for i, item in enumerate(value):
        assert isinstance(item, Mapping)
        yield i, item


def _duplicates(where: str, items: object, what: str) -> Iterator[str]:
    seen: set[object] = set()
    for i, item in _items(items):
        if item["id"] in seen:
            yield f"{where}[{i}].id: duplicate {what} id {item['id']!r}"
        seen.add(item["id"])


def validate(pack: PackV2) -> list[str]:
    problems: list[str] = []
    for path, doc in pack.documents.get("artifacts", {}).items():
        if doc.get("type") != "diagram":
            continue
        spec = doc["spec"]
        assert isinstance(spec, Mapping)
        diagram = spec["diagram"]
        assert isinstance(diagram, Mapping)
        where = f"{path}.spec.diagram"
        problems += _duplicates(f"{where}.actors", diagram["actors"], "actor")
        problems += _duplicates(f"{where}.steps", diagram["steps"], "step")
        actor_ids = {actor["id"] for _, actor in _items(diagram["actors"])}
        for i, step in _items(diagram["steps"]):
            for end in ("from", "to"):
                if step[end] not in actor_ids:
                    problems.append(
                        f"{where}.steps[{i}].{end}: {step[end]!r} is not a declared actor"
                    )
    return problems
