"""Read models the HTTP layer serializes (``contracts/openapi/v0.1.yaml``).

Each view is a frozen dataclass with a ``to_dict()`` in the wire shape of the
matching OpenAPI component, so the API layer only routes, authenticates and
serializes. Pack data reaches a view only through the whitelists of
:mod:`harness.core.pack.model` (AC-J6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from harness.core.pack.model import Definition, PackRef
from harness.core.ports import JsonObject, PlainJson, StoredEvent, to_plain_object

CONTENT_KINDS: tuple[str, ...] = ("skill", "visualization", "reference")


def terminal_path(lab_instance_id: str) -> str:
    """WebSocket path of a lab's terminal (``x-websockets`` in the OpenAPI document)."""
    return f"/labs/{lab_instance_id}/terminal"


def _events(events: Sequence[StoredEvent]) -> list[PlainJson]:
    return [event.to_dict() for event in events]


def pack_provenance(pack: PackRef) -> dict[str, PlainJson]:
    """A pack reference in the wire shape of ``provenance.json#/$defs/pack``."""
    return {
        "pack_id": pack.pack_id,
        "pack_version": pack.pack_version,
        "pack_content_hash": pack.content_hash,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class PackView:
    """One imported pack version (``Pack``, minus ``imported_at``; see the module docs)."""

    pack: PackRef
    title: str
    latest: bool

    def to_dict(self) -> dict[str, PlainJson]:
        return {"pack": pack_provenance(self.pack), "title": self.title, "latest": self.latest}


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionView:
    """``Session``."""

    session_id: str
    learner_id: str
    pack: PackRef
    started_at: str
    session_started_event_id: str

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "session_id": self.session_id,
            "learner_id": self.learner_id,
            "pack": pack_provenance(self.pack),
            "started_at": self.started_at,
            "session_started_event_id": self.session_started_event_id,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class UiState:
    """``UiState``: what a reload has to restore besides chat and highlights."""

    open_content: JsonObject | None
    visualization_steps: Sequence[JsonObject]

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "open_content": None
            if self.open_content is None
            else to_plain_object(self.open_content),
            "visualization_steps": [to_plain_object(step) for step in self.visualization_steps],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityView:
    """``ActivityView``: the only activity fields that may reach the learner (AC-J6)."""

    activity_definition_id: str
    activity_definition_hash: str
    title: str
    activity_type: str
    skill_ids: Sequence[str]
    lab_backed: bool
    instructions: JsonObject

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "activity_definition_id": self.activity_definition_id,
            "activity_definition_hash": self.activity_definition_hash,
            "title": self.title,
            "activity_type": self.activity_type,
            "skill_ids": list(self.skill_ids),
            "lab_backed": self.lab_backed,
            "instructions": to_plain_object(self.instructions),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class LabState:
    """``LabState``."""

    lab_instance_id: str
    attempt_id: str
    status: str
    terminal_id: str | None
    replaced_by_lab_instance_id: str | None

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "lab_instance_id": self.lab_instance_id,
            "attempt_id": self.attempt_id,
            "status": self.status,
            "terminal_id": self.terminal_id,
            "terminal_path": terminal_path(self.lab_instance_id),
            "replaced_by_lab_instance_id": self.replaced_by_lab_instance_id,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptResult:
    """``AttemptResult``: the stored evaluation chain of a completed attempt."""

    outcome: str
    evaluation: StoredEvent
    evidence: Sequence[StoredEvent]
    skill_updates: Sequence[StoredEvent]
    completion: StoredEvent

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "outcome": self.outcome,
            "evaluation": self.evaluation.to_dict(),
            "evidence": _events(self.evidence),
            "skill_updates": _events(self.skill_updates),
            "completion": self.completion.to_dict(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptState:
    """``AttemptState``."""

    attempt_id: str
    session_id: str
    activity: ActivityView
    status: str
    started_at: str
    lab: LabState | None
    last_submission_error: JsonObject | None
    result: AttemptResult | None

    def to_dict(self) -> dict[str, PlainJson]:
        error = self.last_submission_error
        return {
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "activity": self.activity.to_dict(),
            "status": self.status,
            "started_at": self.started_at,
            "lab": None if self.lab is None else self.lab.to_dict(),
            "last_submission_error": None if error is None else to_plain_object(error),
            "result": None if self.result is None else self.result.to_dict(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionState:
    """``SessionState``: everything a reload needs except the long lists (AC-F4)."""

    session: SessionView
    last_position: int
    active_attempt: AttemptState | None
    ui_state: UiState
    last_completed_attempt: AttemptState | None

    def to_dict(self) -> dict[str, PlainJson]:
        active = self.active_attempt
        completed = self.last_completed_attempt
        return {
            "session": self.session.to_dict(),
            "last_position": self.last_position,
            "active_attempt": None if active is None else active.to_dict(),
            "ui_state": self.ui_state.to_dict(),
            "last_completed_attempt": None if completed is None else completed.to_dict(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillStateView:
    """``SkillStateView``: the learner-skill projection of one skill."""

    pack_id: str
    skill_id: str
    state: JsonObject
    update_count: int
    last_update_event_id: str
    updated_at: str

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "pack_id": self.pack_id,
            "skill_id": self.skill_id,
            "state": to_plain_object(self.state),
            "update_count": self.update_count,
            "last_update_event_id": self.last_update_event_id,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentSummary:
    """``ContentSummary``."""

    kind: str
    definition_id: str
    definition_hash: str
    content_version: str
    title: str

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "kind": self.kind,
            "definition_id": self.definition_id,
            "definition_hash": self.definition_hash,
            "content_version": self.content_version,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentDocument:
    """``ContentDocument``: a learner-facing pack document, verbatim."""

    summary: ContentSummary
    document: JsonObject

    def to_dict(self) -> dict[str, PlainJson]:
        return {**self.summary.to_dict(), "document": to_plain_object(self.document)}


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelinePage:
    """``TimelinePage``: events by ``position`` (AC-F2, AC-F7)."""

    events: Sequence[StoredEvent]
    last_position: int
    has_more: bool

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "events": _events(self.events),
            "last_position": self.last_position,
            "has_more": self.has_more,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatExchange:
    """``ChatExchange``: the learner message and the tutor reply, as stored."""

    request: StoredEvent
    reply: StoredEvent

    def to_dict(self) -> dict[str, PlainJson]:
        return {"request": self.request.to_dict(), "reply": self.reply.to_dict()}


# --- builders over pack documents -------------------------------------------


def skill_ids_of_activity(document: JsonObject) -> list[str]:
    """Primary then secondary skills of an activity Definition."""
    skills = document.get("skills")
    if not isinstance(skills, Mapping):
        return []
    out: list[str] = []
    for group in ("primary", "secondary"):
        values = skills.get(group)
        if isinstance(values, Sequence) and not isinstance(values, str):
            out.extend(str(value) for value in values)
    return out


def activity_view(definition: Definition) -> ActivityView:
    """Whitelist view of an activity Definition (AC-J6)."""
    document = definition.document
    instructions = document.get("instructions")
    return ActivityView(
        activity_definition_id=definition.key,
        activity_definition_hash=definition.document_hash,
        title=str(document.get("title", definition.key)),
        activity_type=str(document.get("activity_type", "")),
        skill_ids=skill_ids_of_activity(document),
        lab_backed="environment" in document,
        instructions=instructions if isinstance(instructions, Mapping) else {},
    )


def content_summary(definition: Definition, pack: PackRef) -> ContentSummary:
    """Summary of a learner-facing content document; ``content_version`` falls back
    to the pack version for kinds without their own ``version`` field."""
    document = definition.document
    version = document.get("version")
    return ContentSummary(
        kind=definition.kind,
        definition_id=definition.key,
        definition_hash=definition.document_hash,
        content_version=str(version) if isinstance(version, str) else pack.pack_version,
        title=str(document.get("title", definition.key)),
    )


def content_document(definition: Definition, pack: PackRef) -> ContentDocument:
    return ContentDocument(
        summary=content_summary(definition, pack),
        document=to_plain_object(definition.document),
    )
