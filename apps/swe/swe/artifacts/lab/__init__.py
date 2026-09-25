"""LabArtifact (#62, #95): a disposable lab plus its terminal, as an Artifact subclass,
with its ``/v2`` routes (``routes.router``) and terminal WebSocket (``socket.router``)."""

from swe.artifacts.lab.lab import LAB_SPEC_SCHEMA, ArtifactView, LabArtifact
from swe.artifacts.lab.service import ArtifactError, LabArtifactService
from swe.artifacts.lab.terminal import LabTerminal

__all__ = [
    "LAB_SPEC_SCHEMA",
    "ArtifactError",
    "ArtifactView",
    "LabArtifact",
    "LabArtifactService",
    "LabTerminal",
]
