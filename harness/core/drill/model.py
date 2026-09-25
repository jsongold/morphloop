"""Drill item and answer values (#34, #61).

An item is teaching material with no learner scope: pack-bundled and generated
items share this shape and are told apart only by the ``origin:pack`` /
``origin:generated`` label added when the item is read. The item's
``expected`` answer never leaves the harness toward the learner
(:meth:`DrillItem.for_learner`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from harness.core.ports.json_types import JsonObject, PlainJson

type AnswerMode = Literal["text", "choice", "artifact"]
type Origin = Literal["pack", "generated"]

ANSWERED = "drill.answered"


@dataclass(frozen=True, slots=True, kw_only=True)
class DrillItem:
    """One drill item (``contracts/schemas/pack/v2/drill-item.json``) plus its origin label."""

    id: str
    question: str
    expected: str
    answer_mode: AnswerMode
    labels: tuple[str, ...]
    choices: tuple[str, ...] | None = None
    artifact_ref: str | None = None

    @classmethod
    def from_document(cls, doc: JsonObject, *, origin: Origin) -> DrillItem:
        """Build from a schema-valid drill-item document."""
        labels = doc["labels"]
        choices = doc.get("choices")
        artifact_ref = doc.get("artifact_ref")
        assert isinstance(labels, Sequence)
        return cls(
            id=str(doc["id"]),
            question=str(doc["question"]),
            expected=str(doc["expected"]),
            answer_mode=cast(AnswerMode, str(doc["answer_mode"])),
            labels=(*(str(label) for label in labels), f"origin:{origin}"),
            choices=tuple(str(c) for c in choices) if isinstance(choices, Sequence) else None,
            artifact_ref=None if artifact_ref is None else str(artifact_ref),
        )

    def for_learner(self) -> dict[str, PlainJson]:
        """Learner-facing form: everything except ``expected``."""
        out: dict[str, PlainJson] = {
            "id": self.id,
            "question": self.question,
            "answer_mode": self.answer_mode,
            "labels": list(self.labels),
        }
        if self.choices is not None:
            out["choices"] = list(self.choices)
        if self.artifact_ref is not None:
            out["artifact_ref"] = self.artifact_ref
        return out
