"""LabArtifact (#62, #34): a disposable lab, as an Artifact subclass."""

from harness.core.artifact_lab.lab import ArtifactView, LabArtifact
from harness.core.artifact_lab.service import (
    ArtifactError,
    LabArtifactService,
)

__all__ = ["ArtifactError", "ArtifactView", "LabArtifact", "LabArtifactService"]
