"""The ``lab`` artifact type and the ``artifact`` view (#62, #95).

:class:`LabArtifact` is read from a pack artifact spec (``pack/v2/artifact-spec.json``,
``type: lab``); the shape of its ``spec`` is :data:`LAB_SPEC_SCHEMA`, owned by this type,
not by the SDK contracts (ADR-0018 §19). :class:`ArtifactView` keeps one document per
artifact instance, keyed by ``artifact_id``, built from ``artifact.started`` / ``reset`` /
``stopped``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from harness.sdk import (
    Artifact,
    JsonObject,
    PackV2,
    StoredEventV2,
    View,
    ViewDocumentStore,
    to_plain_json,
)

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
    def learner_view(cls, spec: JsonObject) -> JsonObject:
        """Runtime fixture, parameters and check ids stay hidden from the learner."""
        return {}

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
    status: running|stopped, position, lab}``. ``position`` (the started event's
    DB-assigned order, ADR-0008) is always written by ``artifact.started`` and kept
    only to list in creation order."""

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
                    "position": event.position,
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

    @classmethod
    def list_for_ws(
        cls, tx: ViewDocumentStore, ws_id: str, *, user_id: str, spec_id: str | None = None
    ) -> list[JsonObject]:
        """The learner's artifacts in ``ws_id``, in creation order.

        Type-neutral (``artifact_id``, ``type``, ``spec_id``, ``status`` only):
        a type's own runtime state (this type's ``lab``, another type's own
        key) is that type's concern, not a cross-type list's (ADR-0018 s19).
        """
        # ponytail: scans every artifact view document; key by ws_id if this grows.
        matches = [
            document
            for _, document in cls.list(tx)
            if document["ws_id"] == ws_id
            and document["user_id"] == user_id
            and (spec_id is None or document["spec_id"] == spec_id)
        ]
        matches.sort(key=lambda document: int(document["position"]))  # type: ignore[arg-type]
        return [
            {
                "artifact_id": document["artifact_id"],
                "type": document["type"],
                "spec_id": document["spec_id"],
                "status": document["status"],
            }
            for document in matches
        ]
