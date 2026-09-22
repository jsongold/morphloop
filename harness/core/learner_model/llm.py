"""The v0.1 learner model: an LLM implementation (ADR-0013), ``llm-learner-model@0.1.0``.

Everything comes from the pack selection (ADR-0002): provider, model, prompt
id/version and generation parameters (``llm``), the output schema, the context
budget and ``options``. Options (all required, no others accepted):

``max_mastery_delta`` (number in (0, 1])
    The furthest one update may move ``mastery_probability`` from the previous
    state (the per-update cap ADR-0013 leaves to the pack). Not applied to the
    first update of a skill, which has no previous state.

Request: the system message is the pack prompt text; the user message is a JSON
object ``{"pack_id", "skill", "previous_state", "evidence"}`` (``skill`` is the
SkillDefinition, ``evidence`` the evidence records oldest first).

Context budget: the request size is estimated as
``ceil(characters / _CHARS_PER_TOKEN)``. This is a conservative size estimate,
not a tuning value; a request over ``context_budget_tokens`` raises
:class:`ContextBudgetError` instead of silently dropping evidence, so the caller
decides what to send.

Output checks (AC-E4): the output must validate against
``learner_model.update/1.json``; ``evidence_ids`` must be a subset of the
evidence sent; the mastery cap must hold. Any failure raises
:class:`~harness.core.learner_model.types.LearnerModelOutputError` and changes
no state.
"""

from __future__ import annotations

import json
import math

from harness.core.contract_schemas import ContractSchemas
from harness.core.learner_model.types import (
    LearnerModel,
    LearnerModelInput,
    LearnerModelOutputError,
    LearnerSkillUpdate,
    SkillState,
)
from harness.core.ports import JsonValue, LLMMessage, LLMRequest, to_plain_object
from harness.core.registry.algorithms import (
    LearnerModelDependencies,
    RegistrySelection,
    SelectionError,
)

IMPLEMENTATION = "llm-learner-model@0.1.0"
OUTPUT_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/llm/learner_model.update/1.json"
_OPTIONS = frozenset({"max_mastery_delta"})
_CHARS_PER_TOKEN = 3
_EPSILON = 1e-9


class ContextBudgetError(ValueError):
    """The request would exceed the pack's ``context_budget_tokens``."""


def _max_delta(selection: RegistrySelection) -> float:
    options = selection.options
    missing = sorted(_OPTIONS - set(options))
    if missing:
        raise SelectionError(f"registry.{selection.role}.options: missing {missing}")
    unknown = sorted(set(options) - _OPTIONS)
    if unknown:
        raise SelectionError(f"registry.{selection.role}.options: unknown {unknown}")
    value = options["max_mastery_delta"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise SelectionError(
            f"registry.{selection.role}.options.max_mastery_delta must be in (0, 1]"
        )
    return float(value)


class LLMLearnerModelFactory:
    """Factory registered as :data:`IMPLEMENTATION`."""

    def validate(self, selection: RegistrySelection, schemas: ContractSchemas) -> None:
        if selection.output_schema != OUTPUT_SCHEMA_ID:
            raise SelectionError(
                f"{IMPLEMENTATION} produces {OUTPUT_SCHEMA_ID}, "
                f"but the pack declares {selection.output_schema!r}"
            )
        if not schemas.has(OUTPUT_SCHEMA_ID):
            raise SelectionError(f"output schema {OUTPUT_SCHEMA_ID} is not in contracts/")
        _max_delta(selection)

    def create(self, selection: RegistrySelection, deps: LearnerModelDependencies) -> LearnerModel:
        self.validate(selection, deps.schemas)
        if not deps.prompt:
            raise SelectionError("the learner-model prompt is empty")
        return LLMLearnerModel(
            selection=selection, deps=deps, max_mastery_delta=_max_delta(selection)
        )


class LLMLearnerModel:
    """One pack's learner model; see the module docstring."""

    def __init__(
        self,
        *,
        selection: RegistrySelection,
        deps: LearnerModelDependencies,
        max_mastery_delta: float,
    ) -> None:
        self._selection = selection
        self._deps = deps
        self._max_delta = max_mastery_delta
        self._output_schema = deps.schemas.bundle(OUTPUT_SCHEMA_ID)

    def build_request(self, request: LearnerModelInput) -> LLMRequest:
        """The LLM call for ``request`` (exposed for tests and system logs)."""
        context: dict[str, JsonValue] = {
            "pack_id": request.pack_id,
            "skill": to_plain_object(request.skill_definition),
            "previous_state": None if request.previous is None else request.previous.to_dict(),
            "evidence": [e.to_dict() for e in request.evidence],
        }
        user = json.dumps(context, ensure_ascii=False, sort_keys=True, indent=1)
        estimate = math.ceil((len(self._deps.prompt) + len(user)) / _CHARS_PER_TOKEN)
        if estimate > self._selection.context_budget_tokens:
            raise ContextBudgetError(
                f"learner-model request needs ~{estimate} tokens, "
                f"budget is {self._selection.context_budget_tokens}"
            )
        return LLMRequest(
            role="learner_model",
            llm=self._selection.llm,
            messages=(
                LLMMessage(role="system", content=self._deps.prompt),
                LLMMessage(role="user", content=user),
            ),
            output_schema_id=OUTPUT_SCHEMA_ID,
            output_schema=self._output_schema,
        )

    def update(self, request: LearnerModelInput) -> LearnerSkillUpdate:
        response = self._deps.llm.complete_structured(self.build_request(request))
        output = to_plain_object(response.output)
        errors = self._deps.schemas.errors(output, OUTPUT_SCHEMA_ID)
        if errors:
            raise LearnerModelOutputError("learner-model output fails its schema", errors=errors)
        evidence_ids = output["evidence_ids"]
        assert isinstance(evidence_ids, list)
        given = {e.evidence_id for e in request.evidence}
        unknown = [e for e in evidence_ids if e not in given]
        if unknown:
            raise LearnerModelOutputError(
                f"evidence_ids {unknown} were not given to the model in this call"
            )
        next_raw = output["next"]
        assert isinstance(next_raw, dict)
        next_state = SkillState.from_dict(next_raw)
        if request.previous is not None:
            delta = abs(next_state.mastery_probability - request.previous.mastery_probability)
            if delta > self._max_delta + _EPSILON:
                raise LearnerModelOutputError(
                    f"mastery moved by {delta:.4f}, over max_mastery_delta {self._max_delta}"
                )
        rationale = output["rationale"]
        probability = output["predicted_success_probability"]
        assert isinstance(rationale, str)
        assert isinstance(probability, int | float) and not isinstance(probability, bool)
        return LearnerSkillUpdate(
            pack_id=request.pack_id,
            skill_id=request.skill_id,
            previous=request.previous,
            next=next_state,
            evidence_ids=tuple(str(e) for e in evidence_ids),
            rationale=rationale,
            predicted_success_probability=float(probability),
            provenance=response.provenance,
        )
