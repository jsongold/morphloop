"""DiagramArtifact (#34, #56): wraps a pack diagram artifact-spec as-is."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from harness.core.artifact import artifact_class, registered_artifact_types
from harness.core.artifact_diagram import DiagramArtifact
from harness.core.pack.v2 import import_pack_v2

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"


def _diagram_doc() -> dict[str, Any]:
    pack = import_pack_v2(SE_PACK)
    docs = pack.documents["artifacts"]
    return next(dict(doc) for doc in docs.values() if doc["type"] == "diagram")


def test_registers_as_diagram_with_no_required_capabilities() -> None:
    assert artifact_class("diagram") is DiagramArtifact
    assert registered_artifact_types()["diagram"] is DiagramArtifact
    assert DiagramArtifact.capabilities == frozenset()


def test_from_pack_spec_wraps_the_pack_spec_unchanged() -> None:
    doc = _diagram_doc()
    art = DiagramArtifact.from_pack_spec(doc)
    assert art.id == doc["id"] == "dns-resolution-flow"
    assert art.labels == frozenset(doc["labels"])
    assert art.spec == doc["spec"]
    assert dict(art.spec)["title"].startswith("How ledger.corp.internal")


def test_from_pack_spec_is_frozen_and_rejects_other_types() -> None:
    art = DiagramArtifact.from_pack_spec(_diagram_doc())
    with pytest.raises(FrozenInstanceError):
        art.id = "other"  # type: ignore[misc]
    with pytest.raises(AssertionError):
        DiagramArtifact.from_pack_spec({**_diagram_doc(), "type": "lab"})
