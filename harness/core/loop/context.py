"""Context assembly for the tutor and the evaluator (AC-D5, AC-E1, AC-E2, AC-J6).

Pure functions over data the service has already read, so what an LLM is given
can be asserted in a test without a store, a lab or a provider.

The tutor context is built from learner-facing material only: the whitelist
view of the current activity (:func:`harness.core.pack.learner_view_of_activity`),
the highlights and content the learner selected, the recent events of the
session and the learner's own skill state. The reference solution lives in the
``pack_secret`` projection and is never read on this path;
:func:`assert_no_reference_solution` is a second line of defence that fails
loudly if it ever appears (AC-J6).

The evaluator runs after the learner submitted and may, when the pack says so
(``registry.evaluator.options.include_reference_solution``), also receive the
reference solution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from harness.core.domain_adapter import CheckResult
from harness.core.loop.errors import ReferenceSolutionLeakError
from harness.core.loop.llm_roles import render_context
from harness.core.loop.views import SkillStateView, UiState
from harness.core.pack.model import ReferenceSolution, learner_view_of_activity
from harness.core.ports import (
    JsonObject,
    JsonValue,
    PlainJson,
    StoredEvent,
    format_timestamp,
    to_plain_object,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionContext:
    """The current mission as the tutor may see it (learner view only)."""

    attempt_id: str
    activity_definition_id: str
    activity_definition_hash: str
    status: str
    learner_view: JsonObject

    @property
    def unfinished(self) -> bool:
        return self.status != "completed"

    def to_json(self) -> dict[str, PlainJson]:
        return {
            "attempt_id": self.attempt_id,
            "activity_definition_id": self.activity_definition_id,
            "activity_definition_hash": self.activity_definition_hash,
            "status": self.status,
            "activity": to_plain_object(self.learner_view),
        }


def event_summary(event: StoredEvent) -> dict[str, PlainJson]:
    """One event as an LLM sees it: envelope keys that matter plus the payload."""
    return {
        "event_id": event.event_id,
        "position": event.position,
        "event_type": event.event_type,
        "occurred_at": format_timestamp(event.occurred_at),
        "actor": event.actor,
        "attempt_id": event.attempt_id,
        "payload": to_plain_object(event.payload),
    }


def build_tutor_context(
    *,
    pack_id: str,
    mode: str,
    question: JsonObject,
    mission: MissionContext | None,
    highlights: Sequence[JsonObject],
    ui_state: UiState,
    recent_events: Sequence[StoredEvent],
    learner_skills: Sequence[SkillStateView],
) -> dict[str, JsonValue]:
    """The tutor's context object; ``mode`` is the server's decision (AC-E3)."""
    return {
        "pack_id": pack_id,
        "mode": mode,
        "question": to_plain_object(question),
        "mission": None if mission is None else mission.to_json(),
        "selected_content": {
            "highlights": [to_plain_object(highlight) for highlight in highlights],
            "open_content": ui_state.to_dict()["open_content"],
            "visualization_steps": ui_state.to_dict()["visualization_steps"],
        },
        "recent_events": [event_summary(event) for event in recent_events],
        "learner_skills": [skill.to_dict() for skill in learner_skills],
    }


def tutor_reference_ids(
    highlights: Sequence[JsonObject], recent_events: Sequence[StoredEvent]
) -> list[str]:
    """Ids the tutor reply may cite: the highlights and events given in the call."""
    ids: list[str] = []
    for highlight in highlights:
        highlight_id = highlight.get("highlight_id")
        if isinstance(highlight_id, str):
            ids.append(highlight_id)
    ids.extend(event.event_id for event in recent_events)
    return ids


def assert_no_reference_solution(context: JsonObject, solution: ReferenceSolution | None) -> None:
    """Fail if the solution's explanation leaked into a tutor context (AC-J6)."""
    if solution is None:
        return
    explanation = solution.document.get("explanation")
    if not isinstance(explanation, str) or not explanation:
        return
    if explanation in render_context(context):
        raise ReferenceSolutionLeakError(
            f"the reference solution of {solution.activity_id!r} reached the tutor context"
        )


def build_evaluator_context(
    *,
    pack_id: str,
    attempt_id: str,
    activity: JsonObject,
    evaluator: JsonObject,
    skill_definitions: Sequence[JsonObject],
    checks: Sequence[CheckResult],
    events: Sequence[StoredEvent],
    reference_solution: ReferenceSolution | None,
) -> dict[str, JsonValue]:
    """The evaluator's context object (ADR-0013: it judges observed facts)."""
    context: dict[str, JsonValue] = {
        "pack_id": pack_id,
        "attempt_id": attempt_id,
        "activity": to_plain_object(activity),
        "evaluator": to_plain_object(evaluator),
        "skills": [to_plain_object(skill) for skill in skill_definitions],
        "checks": [check.to_dict() for check in checks],
        "events": [event_summary(event) for event in events],
        "reference_solution": None,
    }
    if reference_solution is not None:
        context["reference_solution"] = to_plain_object(reference_solution.document)
    return context


def learner_activity_view(document: JsonObject) -> dict[str, PlainJson]:
    """The whitelisted activity fields, for a tutor or learner context."""
    return learner_view_of_activity(document)
