"""Learner-model value types and interface (ADR-0004, ADR-0013).

A learner model turns evidence into a new per-skill state. Its result
(:class:`LearnerSkillUpdate`) is what core records on ``learner_skill.updated``
v1 (:meth:`LearnerSkillUpdate.to_payload`); rebuild re-applies the recorded
``next`` state and never calls the model again (ADR-0013). Implementations
never write events themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from harness.core.ports import JsonObject, LLMProvenance, PlainJson, to_plain_object

type Signal = Literal["positive", "negative"]

_UNIT_FIELDS = ("retention_score", "transfer_score", "hint_dependency")


def _unit(name: str, value: float) -> None:
    if isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillState:
    """``contracts/schemas/common/skill-state.json``. ``None`` = not reported."""

    mastery_probability: float
    uncertainty: float
    retention_score: float | None = None
    transfer_score: float | None = None
    hint_dependency: float | None = None
    misconceptions: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _unit("mastery_probability", self.mastery_probability)
        _unit("uncertainty", self.uncertainty)
        for name in _UNIT_FIELDS:
            value = getattr(self, name)
            if value is not None:
                _unit(name, value)

    def to_dict(self) -> dict[str, PlainJson]:
        out: dict[str, PlainJson] = {
            "mastery_probability": self.mastery_probability,
            "uncertainty": self.uncertainty,
        }
        for name in _UNIT_FIELDS:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.misconceptions is not None:
            out["misconceptions"] = list(self.misconceptions)
        return out

    @classmethod
    def from_dict(cls, data: JsonObject) -> SkillState:
        """Build from a schema-valid skill-state object."""

        def number(name: str) -> float | None:
            value = data.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
            return float(value)

        mastery = number("mastery_probability")
        uncertainty = number("uncertainty")
        if mastery is None or uncertainty is None:
            raise ValueError("mastery_probability and uncertainty are required")
        misconceptions = data.get("misconceptions")
        if misconceptions is not None and (
            isinstance(misconceptions, str | Mapping)
            or not isinstance(misconceptions, Sequence)
            or not all(isinstance(m, str) for m in misconceptions)
        ):
            raise ValueError("misconceptions must be an array of strings")
        return cls(
            mastery_probability=mastery,
            uncertainty=uncertainty,
            retention_score=number("retention_score"),
            transfer_score=number("transfer_score"),
            hint_dependency=number("hint_dependency"),
            misconceptions=None
            if misconceptions is None
            else tuple(str(m) for m in misconceptions),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecord:
    """One Evidence item, as ``evidence.created`` v1 records it."""

    evidence_id: str
    evaluation_id: str
    skill_id: str
    signal: Signal
    strength: float
    dimension: str
    rationale: str
    supporting_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _unit("strength", self.strength)

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "evidence_id": self.evidence_id,
            "evaluation_id": self.evaluation_id,
            "skill_id": self.skill_id,
            "signal": self.signal,
            "strength": self.strength,
            "dimension": self.dimension,
            "rationale": self.rationale,
            "supporting_event_ids": list(self.supporting_event_ids),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class LearnerModelInput:
    """One (learner, skill) update request.

    ``skill_definition`` is the pack's SkillDefinition document. ``previous``
    is the current projection state (``None`` before the first update).
    ``evidence`` is ordered oldest first, all for ``skill_id``, ids unique.
    """

    pack_id: str
    skill_id: str
    skill_definition: JsonObject
    previous: SkillState | None
    evidence: Sequence[EvidenceRecord]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("a learner-model update needs at least one evidence record")
        ids = [e.evidence_id for e in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence ids must be unique")
        wrong = [e.evidence_id for e in self.evidence if e.skill_id != self.skill_id]
        if wrong:
            raise ValueError(f"evidence {wrong} is not about skill {self.skill_id!r}")
        if self.skill_definition.get("id") != self.skill_id:
            raise ValueError("skill_definition id does not match skill_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class LearnerSkillUpdate:
    """Validated update result; core stores it on ``learner_skill.updated`` v1."""

    pack_id: str
    skill_id: str
    previous: SkillState | None
    next: SkillState
    evidence_ids: tuple[str, ...]
    rationale: str
    predicted_success_probability: float
    provenance: LLMProvenance

    def to_payload(self) -> dict[str, PlainJson]:
        """The ``learner_skill.updated`` v1 payload."""
        return {
            "pack_id": self.pack_id,
            "skill_id": self.skill_id,
            "previous": None if self.previous is None else self.previous.to_dict(),
            "next": self.next.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "rationale": self.rationale,
            "predicted_success_probability": self.predicted_success_probability,
            "provenance": to_plain_object(self.provenance.to_dict()),
        }


class LearnerModelOutputError(ValueError):
    """The model output failed schema or semantic validation; no state may change."""

    def __init__(self, message: str, *, errors: Sequence[str] = ()) -> None:
        super().__init__(message + ("".join(f"\n{e}" for e in errors)))
        self.errors = tuple(errors)


class LearnerModel(Protocol):
    """A registered learner-model implementation bound to one pack selection."""

    def update(self, request: LearnerModelInput) -> LearnerSkillUpdate:
        """Return the validated update. Raises :class:`LearnerModelOutputError`
        on an invalid output and lets ``LLMError`` propagate."""
        ...
