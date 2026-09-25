"""Test-only stand-ins for the artifact types the dns-pack fixture embeds (#95).

The SDK registers no artifact type (ADR-0018 §19); the real ``lab`` and ``diagram``
types belong to ``apps/swe``. SDK tests import the fixture pack with these stubs:
``LabStub`` bounds the lab spec just enough for the importer tests (schema, then
validator); ``DiagramStub`` accepts any spec.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import ClassVar

from harness.core.artifact import Artifact
from harness.core.pack.v2 import PackV2
from harness.core.ports.json_types import JsonObject


class LabStub(Artifact):
    """``lab`` with the fixture pack's spec shape, no runtime behaviour."""

    type: ClassVar[str] = "lab"
    spec_schema: ClassVar[JsonObject] = {
        "type": "object",
        "required": ["environment", "allowed_fixtures", "allowed_checks"],
        "properties": {"idle_seconds": {"type": "integer", "minimum": 1}},
    }

    @classmethod
    def validate_spec(cls, spec: JsonObject, pack: PackV2) -> Iterable[str]:
        environment, fixtures = spec["environment"], spec["allowed_fixtures"]
        assert isinstance(environment, Mapping) and isinstance(fixtures, Sequence)
        fixture = environment.get("fixture")
        if fixture in fixtures:
            return []
        return [f"environment fixture {fixture!r} is not in allowed_fixtures"]


class DiagramStub(Artifact):
    """``diagram`` with an open spec."""

    type: ClassVar[str] = "diagram"


PACK_ARTIFACT_TYPES: tuple[type[Artifact], ...] = (LabStub, DiagramStub)
