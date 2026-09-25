"""The artifact types the SE pack and the dns-pack fixture embed (#95 part A).

The SDK registers no artifact type; a test that imports one of those packs passes
these explicitly. ``LabArtifact`` still lives in the harness and ``diagram`` has no
implementation yet, so a stub stands in for it. Part B replaces both with the
``apps/swe`` types.
"""

from __future__ import annotations

from typing import ClassVar

from harness.core.artifact import Artifact
from harness.core.artifact_lab import LabArtifact


class DiagramStub(Artifact):
    """``diagram`` with an open spec, until ``apps/swe`` owns the real type."""

    type: ClassVar[str] = "diagram"


PACK_ARTIFACT_TYPES: tuple[type[Artifact], ...] = (LabArtifact, DiagramStub)
