"""DiagramArtifact (#34, #56, #95): a sequence diagram from abstraction to reality.

A ``type: "diagram"`` :class:`~harness.sdk.Artifact` subclass. Its ``spec`` is
exactly the pack artifact-spec's ``spec`` object, validated at import time by
``diagram-spec.json`` (this app's schema, not an SDK contract; ADR-0018 §19) and
:meth:`DiagramArtifact.validate_spec` (declared actors, unique ids). The diagram
is teaching content, not a withheld answer, so there is nothing to filter before
showing it and no client capability is required to render it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from harness.sdk import Artifact, JsonObject, PackV2

DIAGRAM_SPEC_SCHEMA: JsonObject = json.loads(
    (Path(__file__).with_name("diagram-spec.json")).read_text(encoding="utf-8")
)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramArtifact(Artifact):
    """A ``diagram`` artifact: the declarative spec a client renders as-is."""

    type: ClassVar[str] = "diagram"
    spec_schema: ClassVar[JsonObject] = DIAGRAM_SPEC_SCHEMA

    spec: JsonObject

    @classmethod
    def validate_spec(cls, spec: JsonObject, pack: PackV2) -> Iterable[str]:
        """Cross-references the schema cannot express, on a schema-valid ``spec``."""
        diagram = spec["diagram"]
        assert isinstance(diagram, Mapping)
        where = "$.spec.diagram"
        problems = [
            *_duplicates(f"{where}.actors", diagram["actors"], "actor"),
            *_duplicates(f"{where}.steps", diagram["steps"], "step"),
        ]
        actor_ids = {actor["id"] for _, actor in _items(diagram["actors"])}
        for i, step in _items(diagram["steps"]):
            for end in ("from", "to"):
                if step[end] not in actor_ids:
                    problems.append(
                        f"{where}.steps[{i}].{end}: {step[end]!r} is not a declared actor"
                    )
        return problems

    @classmethod
    def from_pack_spec(cls, doc: JsonObject) -> DiagramArtifact:
        """Build from one already schema- and cross-file-validated ``artifacts`` entry."""
        if doc["type"] != cls.type:
            raise ValueError(f"not a diagram artifact-spec: {doc['type']!r}")
        spec = doc["spec"]
        assert isinstance(spec, Mapping)
        labels = doc["labels"]
        assert isinstance(labels, Sequence) and not isinstance(labels, str)
        return cls(
            id=str(doc["id"]),
            labels=frozenset(str(label) for label in labels),
            spec=spec,
        )
