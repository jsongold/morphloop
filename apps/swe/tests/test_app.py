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


def test_lab_openapi_fragment_is_valid_and_not_in_the_sdk_contract() -> None:
    from jsonschema import Draft202012Validator
    from openapi_lab import LAB_SPEC

    from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

    for schema in LAB_SPEC["schemas"].values():
        Draft202012Validator.check_schema(schema)
    check = "/ws/{ws_id}/artifacts/{artifact_id}/check"
    assert check in LAB_SPEC["paths"]
    sdk_paths = load_merged_openapi_v2_spec()["paths"]
    assert check not in sdk_paths
    base = sdk_paths["/ws/{ws_id}/artifacts"]["post"]["responses"]["201"]
    assert "lab" not in base["content"]["application/json"]["schema"]["properties"]
