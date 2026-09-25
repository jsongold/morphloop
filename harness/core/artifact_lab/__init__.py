"""LabArtifact (#62, #34): a disposable lab plus its terminal, as an Artifact subclass."""

from harness.core.artifact_lab.lab import ArtifactView, LabArtifact
from harness.core.artifact_lab.service import (
    ArtifactError,
    LabArtifactService,
)
from harness.core.artifact_lab.terminal import LabTerminal

__all__ = ["ArtifactError", "ArtifactView", "LabArtifact", "LabArtifactService", "LabTerminal"]
