"""DiagramArtifact: a sequence diagram from abstraction to reality (#34, #56).

A ``type: "diagram"`` :class:`~harness.core.artifact.Artifact` subclass. Its
``spec`` is exactly the pack artifact-spec's ``spec`` object (validated at
import time by ``pack/v2/diagram-spec.json`` and
:mod:`harness.core.pack.v2.validators.diagram`): the diagram is
teaching content, not a withheld answer, so there is nothing to filter before
showing it and no client capability is required to render it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from harness.core.artifact import Artifact
from harness.core.ports import JsonObject


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramArtifact(Artifact):
    """A ``diagram`` artifact: the declarative spec a client renders as-is."""

    type: ClassVar[str] = "diagram"

    spec: JsonObject

    @classmethod
    def from_pack_spec(cls, doc: JsonObject) -> DiagramArtifact:
        """Build from one already schema- and cross-file-validated ``artifacts`` entry."""
        assert doc["type"] == "diagram", f"not a diagram artifact-spec: {doc['type']!r}"
        spec = doc["spec"]
        assert isinstance(spec, Mapping)
        labels = doc["labels"]
        assert isinstance(labels, Sequence) and not isinstance(labels, str)
        return cls(
            id=str(doc["id"]),
            labels=frozenset(str(label) for label in labels),
            spec=spec,
        )
