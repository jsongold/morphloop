"""Compute a drill gap outside a transaction, then record it in a short transaction."""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from harness.core.contract_schemas import ContractSchemas, ContractValidationError
from harness.core.drill.model import AnswerMode, DrillItem
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
JUDGED = "drill.judged"
_INPUT_VIEW = "drill.judge_inputs"
# Harness-private namespace: a judgment id is derived from its answer id, so a
# retry finds it without a lookup table. Clients pick answer ids (Idempotency-Key),
# so an event found at the derived id is only a judgment once checked (#124).
_JUDGED_NAMESPACE = uuid.UUID("3c1a5c0e-6b1f-4f1b-9c2e-7d4a2c5e9b11")
_TOKEN = re.compile(r"[\w./:-]+")
_DISTINCTIVE = re.compile(r"[/.:_-]|\d")
_inflight: set[str] = set()
_inflight_guard = threading.Lock()


class DrillJudgeError(ValueError):
    """The answer, LLM output, or gap labels could not be judged safely."""


def judgment_id_of(answer_id: str) -> str:
    """The one ``drill.judged`` id an answer can have."""
    return str(uuid.uuid5(_JUDGED_NAMESPACE, answer_id))


def is_judgment_of(event: StoredEventV2 | None, answer_id: str) -> bool:
    """Whether ``event`` is the ``drill.judged`` of ``answer_id``: type and linkage, not just id."""
    return (
        event is not None
        and event.type == JUDGED
        and event.payload.get("answer_event_id") == answer_id
    )


def stored_judgment(tx: EventTransactionV2, answer_id: str) -> StoredEventV2 | None:
    """The recorded judgment of ``answer_id``; an unrelated event at that id is ``None``."""
    event = tx.get(judgment_id_of(answer_id))
    return event if is_judgment_of(event, answer_id) else None


@contextmanager
def claim_judgment(answer_id: str) -> Iterator[bool]:
    """Single-flight for one answer's judgment: ``True`` for the request that may
    call the LLM; a concurrent retry gets ``False`` and leaves the answer pending.

    ponytail: process-local set; one API process in v0.2. A claim row with a
    TTL in the store when the API runs in more than one process.
    """
    with _inflight_guard:
        claimed = answer_id not in _inflight
        if claimed:
            _inflight.add(answer_id)
    try:
        yield claimed
    finally:
        if claimed:
            with _inflight_guard:
                _inflight.discard(answer_id)


def _tokens(text: str) -> list[str]:
    """Casefolded word tokens; markdown and punctuation fall away, so `*/etc/resolv.conf*`,
    "/ETC/RESOLV.CONF" and "/etc/resolv.conf" agree."""
    return [t.strip("./:-_") for t in _TOKEN.findall(text.casefold()) if t.strip("./:-_")]


def discloses_expected(expected: str, descriptions: Sequence[str]) -> bool:
    """Whether the gap descriptions, read together, give the expected answer away:
    the whole normalized answer, or any of its distinctive tokens (paths,
    identifiers, dotted or hyphenated names, numbers).

    ponytail: token match. A paraphrase or a spelled-out path ("the resolv conf
    file in etc") passes; an LLM leak check when that matters.
    """
    wanted = _tokens(expected)
    shown = _tokens(" ".join(descriptions))
    if not wanted:
        return False
    if any(shown[i : i + len(wanted)] == wanted for i in range(len(shown) - len(wanted) + 1)):
        return True
    distinctive = {t for t in wanted if _DISTINCTIVE.search(t)}
    return not distinctive.isdisjoint(shown)


def artifact_evidence(
    events: Sequence[StoredEventV2], *, answer: StoredEventV2, spec_id: str | None
) -> list[dict[str, object]]:
    """The check facts the judge may see for an artifact answer: the latest result
    of each check on the answer's artifact, made after its last reset and before
    the answer, and only if that artifact was started from the item's own spec.
    """
    artifact_id = answer.payload.get("artifact_id")
    own = [
        e
        for e in events
        if e.user_id == answer.user_id
        and e.ws_id == answer.ws_id
        and e.position < answer.position
        and e.payload.get("artifact_id") == artifact_id
    ]
    if not any(e.type == "artifact.started" and e.payload.get("spec_id") == spec_id for e in own):
        raise DrillJudgeError("the artifact was not started from the item's artifact_ref")
    since = max((e.position for e in own if e.type == "artifact.reset"), default=0)
    latest: dict[str, dict[str, object]] = {}
    for e in own:
        if e.type == "artifact.checked" and e.position > since:
            latest[str(e.payload["check_id"])] = {
                "check_id": e.payload["check_id"],
                "passed": e.payload["passed"],
                "observed": e.payload["observed"],
            }
    if not latest:
        raise DrillJudgeError("no artifact.checked facts for this answer")
    return list(latest.values())


@dataclass(frozen=True, slots=True)
class JudgePack:
    """Pack facts fixed when the answer was written."""

    pack_id: str
    pack_version: str
    pack_hash: str
    labels: frozenset[str]
    topic_ids: tuple[str, ...]

    @classmethod
    def from_pack(cls, pack: PackV2) -> JudgePack:
        return cls(pack.pack_id, pack.pack_version, pack.pack_hash, pack.labels, pack.topic_ids)


def save_judge_inputs(
    tx: EventTransactionV2, answer: StoredEventV2, item: DrillItem, pack: PackV2
) -> None:
    """Save the original item and label rules atomically with a new answer."""
    if tx.get_view(_INPUT_VIEW, answer.id) is not None:
        return
    tx.put_view(
        _INPUT_VIEW,
        answer.id,
        {
            "item": {
                "id": item.id,
                "question": item.question,
                "expected": item.expected,
                "answer_mode": item.answer_mode,
                "labels": list(item.labels),
                "choices": list(item.choices) if item.choices is not None else None,
                "artifact_ref": item.artifact_ref,
            },
            "pack": {
                "pack_id": pack.pack_id,
                "pack_version": pack.pack_version,
                "pack_hash": pack.pack_hash,
                "labels": sorted(pack.labels),
                "topic_ids": list(pack.topic_ids),
            },
        },
    )


def load_judge_inputs(tx: EventTransactionV2, answer_id: str) -> tuple[DrillItem, JudgePack] | None:
    """Read the immutable item and pack facts for a stored answer."""
    doc = tx.get_view(_INPUT_VIEW, answer_id)
    if doc is None:
        return None
    raw_item = cast(JsonObject, doc["item"])
    raw_pack = cast(JsonObject, doc["pack"])
    choices = raw_item["choices"]
    item = DrillItem(
        id=str(raw_item["id"]),
        question=str(raw_item["question"]),
        expected=str(raw_item["expected"]),
        answer_mode=cast(AnswerMode, raw_item["answer_mode"]),
        labels=tuple(cast(Sequence[str], raw_item["labels"])),
        choices=tuple(cast(Sequence[str], choices)) if choices is not None else None,
        artifact_ref=str(raw_item["artifact_ref"])
        if raw_item["artifact_ref"] is not None
        else None,
    )
    pack = JudgePack(
        pack_id=str(raw_pack["pack_id"]),
        pack_version=str(raw_pack["pack_version"]),
        pack_hash=str(raw_pack["pack_hash"]),
        labels=frozenset(cast(Sequence[str], raw_pack["labels"])),
        topic_ids=tuple(cast(Sequence[str], raw_pack["topic_ids"])),
    )
    return item, pack


def compute_gap(
    *,
    answer: StoredEventV2,
    item: DrillItem,
    pack: PackV2 | JudgePack,
    schemas: ContractSchemas,
    llm: LLMProvider | None = None,
    llm_provenance: LLMProvenance | None = None,
    prompt: str | None = None,
    artifact_events: Sequence[StoredEventV2] = (),
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
        if discloses_expected(item.expected, [description]):
            description = "."
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
        context["artifact_checks"] = artifact_evidence(
            artifact_events, answer=answer, spec_id=item.artifact_ref
        )
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
    if discloses_expected(
        item.expected, [str(cast(JsonObject, e)["description"]) for e in missing_output]
    ):
        raise DrillJudgeError("the gap descriptions disclose the expected answer")
    for index, entry in enumerate(missing_output):
        assert isinstance(entry, dict)
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
    answer: StoredEventV2,
    gap: JsonObject,
    pack: PackV2 | JudgePack,
    harness_version: str,
    llm_provenance: LLMProvenance | None,
) -> StoredEventV2:
    """Append the judgment at the answer's derived id. A judgment a concurrent
    retry already stored wins; an unrelated event squatting the id raises
    ``EventIdConflictError`` from the append (409), never a false "complete"."""
    existing = stored_judgment(tx, answer.id)
    if existing is not None:
        return existing
    return tx.append(
        EventV2(
            id=judgment_id_of(answer.id),
            type=JUDGED,
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
