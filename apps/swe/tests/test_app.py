"""The SWE app: the SDK app plus lab and diagram, serving the app's own pack (#95)."""

from __future__ import annotations

import pytest

from swe import PACK_DIR
from swe.app import EXTENSION, create_swe_app
from swe.artifacts.diagram import DiagramArtifact
from swe.artifacts.lab import LabArtifact


def test_app_registers_lab_and_diagram_and_imports_the_se_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MORPHLOOP_PACK_V2_DIR", raising=False)
    app = create_swe_app()
    assert app.state.artifact_types == (LabArtifact, DiagramArtifact) == EXTENSION.artifact_types
    assert app.state.pack_v2.pack_id == "software-engineering"
    assert "/v2/ws/{ws_id}/artifacts/{artifact_id}/check" in app.openapi()["paths"]


def test_env_var_names_another_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    assert not hasattr(create_swe_app().state, "pack_v2")  # PackV2Dep imports it lazily
