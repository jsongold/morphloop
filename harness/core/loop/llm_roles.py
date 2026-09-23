"""The three LLM roles the loop calls directly: evaluator, tutor and the memo summarizer (ADR-0013).

All follow the same shape as the registered learner model
(:mod:`harness.core.learner_model.llm`): the pack declares the provider, model,
prompt and generation parameters; core builds one structured-output request,
checks the size against the pack's context budget, validates the output against
its contract schema and then applies the semantic rules no schema can express
(``contracts/schemas/llm/README.md``). An output that fails changes no state
(AC-E4): these functions raise and append nothing.

The learner model stays in the algorithm registry (ADR-0004). Evaluator, tutor
and the memo summarizer are not registry roles in v0.1
(:meth:`AlgorithmRegistry.governed_roles`), so their selections are read here
and their implementation name is checked against the one v0.1 provides.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from harness.core.contract_schemas import ContractSchemas, ContractValidationError
from harness.core.loop.errors import InvalidRequestError, LLMFailedError
from harness.core.loop.options import (
    EVALUATOR_SCHEMA_ID,
    MEMO_SUMMARIZER_SCHEMA_ID,
    TUTOR_SCHEMA_ID,
    EvaluatorOptions,
    MemoSummarizerOptions,
    TutorOptions,
)
from harness.core.ports import (
    JsonObject,
    JsonValue,
    LLMError,
    LLMMessage,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    PlainJson,
    to_plain_json,
    to_plain_object,
)

_CHARS_PER_TOKEN = 3


def render_context(context: JsonObject) -> str:
    """Stable JSON rendering of a context object for an LLM user message."""
    return json.dumps(to_plain_object(context), ensure_ascii=False, sort_keys=True, indent=1)


def estimate_tokens(*texts: str) -> int:
    """Conservative size estimate; not a tuning value (see the learner model)."""
    return math.ceil(sum(len(text) for text in texts) / _CHARS_PER_TOKEN)


def _check_budget(role: str, budget: int, *texts: str) -> None:
    estimate = estimate_tokens(*texts)
    if estimate > budget:
        raise InvalidRequestError(
            f"{role} context needs ~{estimate} tokens, the pack budget is {budget}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgedEvidence:
    """One evidence item of an evaluator judgment, before the harness adds ids."""

    skill_id: str
    signal: str
    strength: float
    dimension: str
    rationale: str
    supporting_event_ids: tuple[str, ...]

    def payload(self, *, evidence_id: str, evaluation_id: str) -> dict[str, PlainJson]:
        """The ``evidence.created`` v1 payload for this item."""
        return {
            "evidence_id": evidence_id,
            "evaluation_id": evaluation_id,
            "skill_id": self.skill_id,
            "signal": self.signal,
            "strength": self.strength,
            "dimension": self.dimension,
            "rationale": self.rationale,
            "supporting_event_ids": list(self.supporting_event_ids),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorJudgment:
    """Validated ``evaluator.judgment`` output plus the provenance to record."""

    success: bool
    rationale: str
    evidence: tuple[JudgedEvidence, ...]
    provenance: LLMProvenance


@dataclass(frozen=True, slots=True, kw_only=True)
class TutorReply:
    """Validated ``tutor.reply`` output; the mode is decided by the harness."""

    text: str
    references: tuple[JsonObject, ...]
    provenance: LLMProvenance


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoNote:
    """Validated ``memo_summarizer.note`` output plus the provenance to record."""

    title: str
    body: str
    provenance: LLMProvenance


def _call(
    llm: LLMProvider,
    request: LLMRequest,
    schemas: ContractSchemas,
    schema_id: str,
) -> tuple[dict[str, PlainJson], LLMProvenance]:
    try:
        response = llm.complete_structured(request)
    except LLMError as exc:
        raise LLMFailedError(f"{request.role} call failed: {exc}") from exc
    output = to_plain_object(response.output)
    try:
        schemas.validate(output, schema_id)
    except ContractValidationError as exc:
        raise LLMFailedError(
            f"{request.role} output failed {schema_id}", errors=exc.errors
        ) from exc
    return output, response.provenance


def _str_list(value: PlainJson) -> tuple[str, ...]:
    assert isinstance(value, list)
    return tuple(str(item) for item in value)


class LLMEvaluator:
    """``llm-evaluator@0.1.0``: judges one submitted attempt (ADR-0013)."""

    def __init__(
        self,
        *,
        options: EvaluatorOptions,
        prompt: str,
        llm: LLMProvider,
        schemas: ContractSchemas,
    ) -> None:
        if not prompt:
            raise InvalidRequestError("the evaluator prompt is empty")
        self._options = options
        self._prompt = prompt
        self._llm = llm
        self._schemas = schemas
        self._output_schema = schemas.bundle(EVALUATOR_SCHEMA_ID)

    def build_request(self, context: JsonObject) -> LLMRequest:
        user = render_context(context)
        _check_budget("evaluator", self._options.context_budget_tokens, self._prompt, user)
        return LLMRequest(
            role="evaluator",
            llm=self._options.selection.llm,
            messages=(
                LLMMessage(role="system", content=self._prompt),
                LLMMessage(role="user", content=user),
            ),
            output_schema_id=EVALUATOR_SCHEMA_ID,
            output_schema=self._output_schema,
        )

    def judge(
        self,
        context: JsonObject,
        *,
        skills_under_test: Sequence[str],
        dimensions: Sequence[str],
        event_ids: Sequence[str],
    ) -> EvaluatorJudgment:
        """One judgment. Raises :class:`LLMFailedError` without changing state."""
        output, provenance = _call(
            self._llm, self.build_request(context), self._schemas, EVALUATOR_SCHEMA_ID
        )
        raw_evidence = output["evidence"]
        assert isinstance(raw_evidence, list)
        known_skills, known_dimensions = set(skills_under_test), set(dimensions)
        known_events = set(event_ids)
        evidence: list[JudgedEvidence] = []
        for index, item in enumerate(raw_evidence):
            assert isinstance(item, dict)
            skill_id, dimension = str(item["skill_id"]), str(item["dimension"])
            supporting = _str_list(item["supporting_event_ids"])
            where = f"evidence[{index}]"
            if skill_id not in known_skills:
                raise LLMFailedError(
                    f"{where}.skill_id {skill_id!r} is not under test {sorted(known_skills)}"
                )
            if dimension not in known_dimensions:
                raise LLMFailedError(
                    f"{where}.dimension {dimension!r} is not in the pack vocabulary "
                    f"{sorted(known_dimensions)}"
                )
            unknown = [event_id for event_id in supporting if event_id not in known_events]
            if unknown:
                raise LLMFailedError(f"{where}.supporting_event_ids {unknown} were not in the call")
            strength = item["strength"]
            assert isinstance(strength, int | float) and not isinstance(strength, bool)
            evidence.append(
                JudgedEvidence(
                    skill_id=skill_id,
                    signal=str(item["signal"]),
                    strength=float(strength),
                    dimension=dimension,
                    rationale=str(item["rationale"]),
                    supporting_event_ids=supporting,
                )
            )
        success = output["success"]
        assert isinstance(success, bool)
        return EvaluatorJudgment(
            success=success,
            rationale=str(output["rationale"]),
            evidence=tuple(evidence),
            provenance=provenance,
        )


class LLMTutor:
    """``llm-tutor@0.1.0``: one chat reply in the mode the harness decided (AC-E3)."""

    def __init__(
        self,
        *,
        options: TutorOptions,
        prompt: str,
        llm: LLMProvider,
        schemas: ContractSchemas,
    ) -> None:
        if not prompt:
            raise InvalidRequestError("the tutor prompt is empty")
        self._options = options
        self._prompt = prompt
        self._llm = llm
        self._schemas = schemas
        self._output_schema = schemas.bundle(TUTOR_SCHEMA_ID)

    def build_request(self, context: JsonObject) -> LLMRequest:
        user = render_context(context)
        _check_budget("tutor", self._options.context_budget_tokens, self._prompt, user)
        return LLMRequest(
            role="tutor",
            llm=self._options.selection.llm,
            messages=(
                LLMMessage(role="system", content=self._prompt),
                LLMMessage(role="user", content=user),
            ),
            output_schema_id=TUTOR_SCHEMA_ID,
            output_schema=self._output_schema,
        )

    def reply(self, context: JsonObject, *, allowed_reference_ids: Sequence[str]) -> TutorReply:
        """One reply. Raises :class:`LLMFailedError` without changing state."""
        output, provenance = _call(
            self._llm, self.build_request(context), self._schemas, TUTOR_SCHEMA_ID
        )
        raw_references = output["references"]
        assert isinstance(raw_references, list)
        allowed = set(allowed_reference_ids)
        references: list[JsonObject] = []
        for index, item in enumerate(raw_references):
            assert isinstance(item, dict)
            reference_id = str(item["id"])
            if reference_id not in allowed:
                raise LLMFailedError(
                    f"references[{index}] {reference_id!r} was not given to the tutor in this call"
                )
            references.append(item)
        return TutorReply(
            text=str(output["text"]), references=tuple(references), provenance=provenance
        )


class LLMMemoSummarizer:
    """``llm-memo-summarizer@0.1.0``: writes one learning note per highlight thread."""

    def __init__(
        self,
        *,
        options: MemoSummarizerOptions,
        prompt: str,
        llm: LLMProvider,
        schemas: ContractSchemas,
    ) -> None:
        if not prompt:
            raise InvalidRequestError("the memo summarizer prompt is empty")
        self._options = options
        self._prompt = prompt
        self._llm = llm
        self._schemas = schemas
        self._output_schema = schemas.bundle(MEMO_SUMMARIZER_SCHEMA_ID)

    def build_request(self, context: JsonObject) -> LLMRequest:
        user = render_context(context)
        _check_budget(
            "memo_summarizer", self._options.context_budget_tokens, self._prompt, user
        )
        return LLMRequest(
            role="memo_summarizer",
            llm=self._options.selection.llm,
            messages=(
                LLMMessage(role="system", content=self._prompt),
                LLMMessage(role="user", content=user),
            ),
            output_schema_id=MEMO_SUMMARIZER_SCHEMA_ID,
            output_schema=self._output_schema,
        )

    def summarize(self, context: JsonObject) -> MemoNote:
        """One note. Raises :class:`LLMFailedError` without changing state."""
        output, provenance = _call(
            self._llm, self.build_request(context), self._schemas, MEMO_SUMMARIZER_SCHEMA_ID
        )
        return MemoNote(title=str(output["title"]), body=str(output["body"]), provenance=provenance)


def json_value(value: Mapping[str, JsonValue]) -> JsonValue:
    """Narrow a mapping to :data:`JsonValue` for embedding in a context object."""
    return to_plain_json(value)
