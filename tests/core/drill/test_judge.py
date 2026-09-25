from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.drill.judge import GAP_SCHEMA_ID, DrillJudgeError, compute_gap, record_judgment
from harness.core.drill.store import pack_items
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.events_v2 import StoredEventV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK = Path(__file__).resolve().parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"
PROVENANCE = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="gap",
    prompt_version="1",
    generation_parameters={},
)


class FakeLLM:
    def __init__(self, output: JsonObject) -> None:
        self.output = output
        self.request: LLMRequest | None = None

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.request = request
        return LLMResponse(output=self.output, provenance=PROVENANCE)


def _event(item_id: str, mode: str, **payload: str) -> StoredEventV2:
    return StoredEventV2(
        id=str(uuid.uuid4()),
        type="drill.answered",
        actor="learner",
        user_id="usr_1",
        session_id="ses_1",
        ws_id="ws_1",
        payload={"item_id": item_id, "answer_mode": mode, **payload},
        position=2,
        created_at=datetime.now(UTC),
    )


def test_choice_correct_and_incorrect_hide_expected() -> None:
    schemas = ContractSchemas.load()
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-record-choice")
    correct, provenance = compute_gap(
        answer=_event(item.id, "choice", actual=item.expected),
        item=item,
        pack=pack,
        schemas=schemas,
    )
    assert correct == {"missing": []} and provenance is None
    answer = _event(item.id, "choice", actual="AAAA")
    gap, provenance = compute_gap(answer=answer, item=item, pack=pack, schemas=schemas)
    assert len(gap["missing"]) == 1  # type: ignore[arg-type]
    assert item.expected not in str(gap)
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        judged = record_judgment(
            tx,
            event_id=str(uuid.uuid4()),
            answer=answer,
            gap=gap,
            pack=pack,
            harness_version="0.2.0",
            llm_provenance=provenance,
        )
    assert judged.actor == "system" and judged.payload["answer_event_id"] == answer.id
    assert item.expected not in str(judged.payload["gap"])


def test_text_uses_structured_output_and_rejects_invalid_or_leaking_output() -> None:
    schemas = ContractSchemas.load()
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-resolver-text")
    answer = _event(item.id, "text", actual="I do not know")
    llm = FakeLLM(
        {
            "missing": [
                {
                    "description": "Review resolver configuration.",
                    "labels": ["topic:network.dns.resolution"],
                }
            ]
        }
    )
    gap, provenance = compute_gap(
        answer=answer,
        item=item,
        pack=pack,
        schemas=schemas,
        llm=llm,
        llm_provenance=PROVENANCE,
        prompt="Judge the gap.",
    )
    assert gap["missing"] and provenance == PROVENANCE
    assert llm.request is not None and llm.request.output_schema_id == GAP_SCHEMA_ID
    assert "without quoting the expected answer" in llm.request.messages[0].content
    for output in (
        {"missing": [{"description": item.expected, "labels": []}]},
        {"missing": [{"description": "Review this.", "labels": ["not-in-pack"]}]},
        {"missing": [{"description": "", "labels": []}]},
    ):
        with pytest.raises(DrillJudgeError):
            compute_gap(
                answer=answer,
                item=item,
                pack=pack,
                schemas=schemas,
                llm=FakeLLM(output),
                llm_provenance=PROVENANCE,
                prompt="Judge the gap.",
            )


def test_artifact_gap_uses_only_prior_matching_check_facts() -> None:
    schemas = ContractSchemas.load()
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", artifact_id="art_1")
    check = StoredEventV2(
        id=str(uuid.uuid4()),
        type="artifact.checked",
        actor="system",
        user_id="usr_1",
        session_id="ses_1",
        ws_id="ws_1",
        payload={
            "artifact_id": "art_1",
            "check_id": "dns.check",
            "passed": False,
            "observed": {"result": "bad"},
        },
        position=1,
        created_at=datetime.now(UTC),
    )
    llm = FakeLLM({"missing": []})
    with pytest.raises(DrillJudgeError, match="no artifact.checked"):
        compute_gap(
            answer=answer,
            item=item,
            pack=pack,
            schemas=schemas,
            llm=llm,
            llm_provenance=PROVENANCE,
            prompt="Judge the gap.",
        )
    compute_gap(
        answer=answer,
        item=item,
        pack=pack,
        schemas=schemas,
        llm=llm,
        llm_provenance=PROVENANCE,
        prompt="Judge the gap.",
        artifact_checks=[check],
    )
    assert (
        llm.request is not None
        and '"observed": {"result": "bad"}' in llm.request.messages[1].content
    )
