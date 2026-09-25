"""The ``lab`` artifact type and the ``artifact`` view (#62).

:class:`LabArtifact` is read from a pack artifact spec (``pack/v2/artifact-spec.json``,
``type: lab``); the shape of its ``spec`` is :data:`LAB_SPEC_SCHEMA`, owned by this type,
not by the contract (#95). :class:`ArtifactView` keeps one document per artifact instance,
keyed by ``artifact_id``, built from ``artifact.started`` / ``reset`` / ``stopped``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from harness.core.artifact import Artifact
from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject, to_plain_json
from harness.core.view import View

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

_CONTRACTS = "https://morphloop.dev/contracts/schemas/"
_ADAPTER_ITEM_IDS: JsonObject = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {"$ref": _CONTRACTS + "common/ids.json#/$defs/adapter_item_id"},
}
LAB_SPEC_SCHEMA: JsonObject = {
    "title": "Lab spec",
    "description": (
        "A disposable lab plus the bounds for generating variants of it. The generator "
        "may compose only the listed fixtures and checks (adapter item ids)."
    ),
    "type": "object",
    "required": ["environment", "allowed_fixtures", "allowed_checks"],
    "properties": {
        "environment": {"$ref": _CONTRACTS + "pack/environment.json"},
        "allowed_fixtures": _ADAPTER_ITEM_IDS,
        "allowed_checks": _ADAPTER_ITEM_IDS,
        "idle_seconds": {
            "description": (
                "Stop the lab after its ws has had no learner activity for this many "
                "seconds. Absent: the lab stops only when asked (or at the runtime's "
                "lifetime limit)."
            ),
            "type": "integer",
            "minimum": 1,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True, kw_only=True)
class LabArtifact(Artifact):
    """A lab: its environment plus the fixtures and checks it may use."""

    type: ClassVar[str] = "lab"
    capabilities: ClassVar[frozenset[str]] = frozenset({"terminal"})
    spec_schema: ClassVar[JsonObject] = LAB_SPEC_SCHEMA

    spec_id: str
    environment: JsonObject
    allowed_checks: frozenset[str]
    idle_seconds: int | None = None
    """Stop the lab after this long without ws activity; ``None``: only on request."""

    @classmethod
    def validate_spec(cls, spec: JsonObject, pack: PackV2) -> Iterable[str]:
        environment, fixtures = spec["environment"], spec["allowed_fixtures"]
        assert isinstance(environment, Mapping) and isinstance(fixtures, Sequence)
        if environment.get("fixture") not in fixtures:
            return [
                f"environment fixture {environment.get('fixture')!r} is not in allowed_fixtures"
            ]
        return ()

    @classmethod
    def from_spec(cls, artifact_id: str, spec: JsonObject) -> LabArtifact:
        """Build from a schema-valid artifact spec; raises ``ValueError`` if it is not usable."""
        if spec.get("type") != cls.type:
            raise ValueError(f"artifact spec {spec.get('id')!r} is not a {cls.type!r} spec")
        body = spec["spec"]
        assert isinstance(body, Mapping)
        environment = body["environment"]
        fixtures, checks, labels = body["allowed_fixtures"], body["allowed_checks"], spec["labels"]
        assert isinstance(environment, Mapping)
        assert isinstance(fixtures, Sequence) and isinstance(checks, Sequence)
        assert isinstance(labels, Sequence)
        idle = body.get("idle_seconds")
        if environment.get("fixture") not in fixtures:
            raise ValueError(f"fixture {environment.get('fixture')!r} is not in allowed_fixtures")
        return cls(
            id=artifact_id,
            labels=frozenset(str(label) for label in labels),
            spec_id=str(spec["id"]),
            environment=environment,
            allowed_checks=frozenset(str(c) for c in checks),
            idle_seconds=idle if isinstance(idle, int) else None,
        )


class ArtifactView(View):
    """``artifact`` view: ``{artifact_id, type, spec_id, user_id, session_id, ws_id,
    status: running|stopped, lab}``."""

    name = "artifact"
    handles = frozenset({"artifact.started", "artifact.reset", "artifact.stopped"})

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        payload = event.payload
        artifact_id = str(payload["artifact_id"])
        if event.type == "artifact.started":
            tx.put_view(
                cls.name,
                artifact_id,
                {
                    "artifact_id": artifact_id,
                    "type": payload["type"],
                    "spec_id": payload["spec_id"],
                    "user_id": event.user_id,
                    "session_id": event.session_id,
                    "ws_id": event.ws_id,
                    "status": "running",
                    "lab": to_plain_json(payload.get("lab")),
                },
            )
            return
        document = dict(tx.get_view(cls.name, artifact_id) or {})
        if event.type == "artifact.reset":
            document["lab"] = to_plain_json(payload["lab"])
        else:
            document["status"] = "stopped"
        tx.put_view(cls.name, artifact_id, document)
