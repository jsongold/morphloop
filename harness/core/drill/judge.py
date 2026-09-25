"""Compute a drill gap outside a transaction, then record it in a short transaction."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from harness.core.contract_schemas import ContractSchemas, ContractValidationError
from harness.core.drill.model import DrillItem
from harness.core.labels import LabelError, check_labels
from harness.core.pack.v2.importer import PackV2
from harness.core.ports.events_v2 import EventTransactionV2, EventV2, StoredEventV2
from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_object
from harness.core.ports.llm import (
    LLMError,
    LLMMessage,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    LLMRole,
)

GAP_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/llm/drill.gap/1.json"


class DrillJudgeError(ValueError):
    """The answer, LLM output, or gap labels could not be judged safely."""


def compute_gap(
    *,
    answer: StoredEventV2,
    item: DrillItem,
    pack: PackV2,
    schemas: ContractSchemas,
    llm: LLMProvider | None = None,
    llm_provenance: LLMProvenance | None = None,
    prompt: str | None = None,
    artifact_checks: Sequence[StoredEventV2] = (),
) -> tuple[dict[str, PlainJson], LLMProvenance | None]:
    """Return a validated gap and the LLM settings actually used, without writing events."""
    if (
        answer.type != "drill.answered"
        or answer.payload.get("item_id") != item.id
        or answer.payload.get("answer_mode") != item.answer_mode
    ):
        raise DrillJudgeError("answer event does not match the drill item")
    labels = [label for label in item.labels if not label.startswith("origin:")]
    actual = answer.payload.get("actual")
    if item.answer_mode == "choice":
        if not isinstance(actual, str):
            raise DrillJudgeError("choice answer has no actual value")
        description = "Review the concept in the item labels."
        if item.expected in description:
            description = "." if item.expected != "." else "!"
        missing: list[PlainJson] = (
            []
            if actual == item.expected
            else [cast(PlainJson, {"description": description, "labels": labels})]
        )
        return {"missing": missing}, None

    if llm is None or llm_provenance is None or not prompt:
        raise DrillJudgeError("text and artifact judgments need an LLM and pack prompt")
    context: dict[str, object] = {
        "question": item.question,
        "expected": item.expected,
        "labels": labels,
    }
    if item.answer_mode == "text":
        if not isinstance(actual, str):
            raise DrillJudgeError("text answer has no actual value")
        context["actual"] = actual
    else:
        artifact_id = answer.payload.get("artifact_id")
        context["artifact_checks"] = [
            {
                "check_id": check.payload["check_id"],
                "passed": check.payload["passed"],
                "observed": check.payload["observed"],
            }
            for check in artifact_checks
            if check.type == "artifact.checked"
            and check.user_id == answer.user_id
            and check.ws_id == answer.ws_id
            and check.position < answer.position
            and check.payload.get("artifact_id") == artifact_id
        ]
        if not context["artifact_checks"]:
            raise DrillJudgeError("no artifact.checked facts for this answer")
    try:
        response = llm.complete_structured(
            LLMRequest(
                role=cast(LLMRole, llm_provenance.prompt_id),
                llm=llm_provenance,
                messages=(
                    LLMMessage(
                        role="system",
                        content=prompt
                        + "\nDescribe missing concepts without quoting the expected answer.",
                    ),
                    LLMMessage(
                        role="user", content=json.dumps(context, ensure_ascii=False, sort_keys=True)
                    ),
                ),
                output_schema_id=GAP_SCHEMA_ID,
                output_schema=schemas.bundle(GAP_SCHEMA_ID),
            )
        )
    except LLMError as exc:
        raise DrillJudgeError(f"drill gap call failed: {exc}") from exc
    try:
        schemas.validate(response.output, GAP_SCHEMA_ID)
    except ContractValidationError as exc:
        raise DrillJudgeError(f"drill gap output failed schema validation: {exc.errors}") from exc
    gap = to_plain_object(response.output)
    missing_output = gap["missing"]
    assert isinstance(missing_output, list)
    for index, entry in enumerate(missing_output):
        assert isinstance(entry, dict)
        description = str(entry["description"])
        if item.expected in description:
            raise DrillJudgeError(f"missing[{index}].description quotes the expected answer")
        entry_labels = entry["labels"]
        assert isinstance(entry_labels, list)
        try:
            check_labels(
                cast(list[str], entry_labels), vocabulary=pack.labels, topic_ids=pack.topic_ids
            )
        except LabelError as exc:
            raise DrillJudgeError(f"missing[{index}].labels are not in the pack: {exc}") from exc
    return gap, response.provenance


def record_judgment(
    tx: EventTransactionV2,
    *,
    event_id: str,
    answer: StoredEventV2,
    gap: JsonObject,
    pack: PackV2,
    harness_version: str,
    llm_provenance: LLMProvenance | None,
) -> StoredEventV2:
    """Append the computed judgment; a resend with the same event id is idempotent."""
    return tx.append(
        EventV2(
            id=event_id,
            type="drill.judged",
            actor="system",
            user_id=answer.user_id,
            session_id=answer.session_id,
            ws_id=answer.ws_id,
            payload={
                "answer_event_id": answer.id,
                "gap": gap,
                "provenance": {
                    "harness_version": harness_version,
                    "pack": {
                        "pack_id": pack.pack_id,
                        "pack_version": pack.pack_version,
                        "pack_content_hash": pack.pack_hash,
                    },
                    "llm": llm_provenance.to_dict() if llm_provenance is not None else None,
                },
            },
        )
    ).event
