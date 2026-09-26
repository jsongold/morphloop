from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.drill.judge import (
    GAP_SCHEMA_ID,
    DrillJudgeError,
    claim_judgment,
    compute_gap,
    discloses_expected,
    judgment_id_of,
    load_judge_inputs,
    record_judgment,
    save_judge_inputs,
    stored_judgment,
)
from harness.core.drill.store import pack_items
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.events_v2 import EventIdConflictError, EventV2, StoredEventV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.testing.claims import InMemoryClaimStore
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


def _event(item_id: str, mode: str, position: int = 2, **payload: str) -> StoredEventV2:
    return StoredEventV2(
        id=str(uuid.uuid4()),
        type="drill.answered",
        actor="learner",
        user_id="usr_1",
        session_id="ses_1",
        ws_id="ws_1",
        payload={"item_id": item_id, "answer_mode": mode, **payload},
        position=position,
        created_at=datetime.now(UTC),
    )


def _artifact_event(
    type_: str, position: int, ws_id: str = "ws_1", **payload: object
) -> StoredEventV2:
    return StoredEventV2(
        id=str(uuid.uuid4()),
        type=type_,
        actor="system",
        user_id="usr_1",
        session_id="ses_1",
        ws_id=ws_id,
        payload={"artifact_id": "art_1", **payload},  # type: ignore[dict-item]
        position=position,
        created_at=datetime.now(UTC),
    )


TARGET_PARAMS: dict[str, JsonObject] = {
    "dns.name_resolves": {"name": "api.internal"},
    "dns.command_exit": {"argv": ["curl", "-fsS", "http://api.internal/"], "expected_exit_code": 0},
}


def _check(
    position: int,
    check_id: str = "dns.name_resolves",
    passed: bool = False,
    params: JsonObject | None = None,
) -> StoredEventV2:
    return _artifact_event(
        "artifact.checked",
        position,
        check_id=check_id,
        passed=passed,
        observed={"p": position},
        params=TARGET_PARAMS[check_id] if params is None else params,
    )


def _command(position: int) -> StoredEventV2:
    return _artifact_event(
        "artifact.input",
        position,
        labels=["io:command"],
        sequence=0,
        data="rm -f x",
        encoding="utf-8",
    )


STARTED = _artifact_event("artifact.started", 1, type="lab", spec_id="dns-broken-resolver-lab")


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


def _judge(item, answer, llm, **kw):  # type: ignore[no-untyped-def]
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    return compute_gap(
        answer=answer,
        item=item,
        pack=pack,
        schemas=ContractSchemas.load(),
        llm=llm,
        llm_provenance=PROVENANCE,
        prompt="Judge the gap.",
        **kw,
    )


def test_artifact_gap_uses_only_prior_matching_check_facts() -> None:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    llm = FakeLLM({"missing": []})
    with pytest.raises(DrillJudgeError, match="not started from the item"):
        _judge(item, answer, llm, artifact_events=[_check(2)])
    with pytest.raises(DrillJudgeError, match="no artifact.checked"):
        _judge(item, answer, llm, artifact_events=[STARTED])
    _judge(
        item,
        answer,
        llm,
        artifact_events=[
            STARTED,
            _check(2, "dns.name_resolves", passed=True),
            _check(3, "dns.command_exit", passed=True),
            _check(9, "dns.name_resolves", passed=True),  # after the answer
        ],
    )
    assert llm.request is not None
    sent = json.loads(llm.request.messages[1].content)["artifact_checks"]
    assert {c["check_id"]: c["observed"] for c in sent} == {
        "dns.name_resolves": {"p": 2},
        "dns.command_exit": {"p": 3},
    }
    assert '"p": 9' not in llm.request.messages[1].content  # after the answer


def test_artifact_checks_must_come_from_the_items_own_spec() -> None:
    # #124: another drill's artifact (same id pattern, other spec) is not evidence
    # for this item, nor is a check from another ws.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    other_spec = _artifact_event(
        "artifact.started", 1, type="diagram", spec_id="dns-resolution-flow"
    )
    with pytest.raises(DrillJudgeError, match="not started from the item"):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=[other_spec, _check(2)])
    other_ws = _artifact_event(
        "artifact.started", 1, ws_id="ws_2", type="lab", spec_id=item.artifact_ref
    )
    with pytest.raises(DrillJudgeError, match="not started from the item"):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=[other_ws, _check(2)])


def test_artifact_checks_before_a_reset_are_dropped_and_latest_per_check_wins() -> None:
    # #124: pass a check, reset the lab, submit -- the pre-reset pass is gone.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=9, artifact_id="art_1")
    reset = _artifact_event("artifact.reset", 3, replaces_lab_instance_id="lab_1", lab={})
    llm = FakeLLM({"missing": []})
    with pytest.raises(DrillJudgeError, match="no artifact.checked"):
        _judge(item, answer, llm, artifact_events=[STARTED, _check(2, passed=True), reset])
    _judge(
        item,
        answer,
        llm,
        artifact_events=[
            STARTED,
            _check(2, "dns.name_resolves", passed=True),
            reset,
            _check(4, "dns.name_resolves"),
            _check(5, "dns.name_resolves", passed=True),
            _check(6, "dns.command_exit", passed=True),
        ],
    )
    assert llm.request is not None
    sent = json.loads(llm.request.messages[1].content)["artifact_checks"]
    assert sent == [
        {"check_id": "dns.name_resolves", "passed": True, "observed": {"p": 5}},
        {"check_id": "dns.command_exit", "passed": True, "observed": {"p": 6}},
    ]


def test_artifact_gap_cannot_be_empty_when_a_check_failed() -> None:
    # #124 review: a failed deterministic check is a gap whatever the model says,
    # so a schema-valid `{"missing": []}` must not be recorded as no gap.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    events = [
        STARTED,
        _check(2, "dns.name_resolves"),
        _check(3, "dns.command_exit", passed=True),
    ]
    with pytest.raises(DrillJudgeError, match="cannot be empty"):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=events)
    gap, _ = _judge(
        item,
        answer,
        FakeLLM(
            {
                "missing": [
                    {"description": "Review resolver configuration.", "labels": ["troubleshooting"]}
                ]
            }
        ),
        artifact_events=events,
    )
    assert gap["missing"]
    _judge(
        item,
        answer,
        FakeLLM({"missing": []}),
        artifact_events=[
            STARTED,
            _check(2, "dns.name_resolves", passed=True),
            _check(3, "dns.command_exit", passed=True),
        ],
    )


def test_judge_inputs_snapshot_carries_the_items_required_checks() -> None:
    # #124 review: the retry path judges from the snapshot, so the item's check
    # set must be persisted with it or the full-set rule is lost.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        save_judge_inputs(tx, answer, item, pack)
        loaded = load_judge_inputs(tx, answer.id)
    assert loaded is not None
    assert loaded[0].required_checks == tuple(
        {"check": check, "params": params} for check, params in TARGET_PARAMS.items()
    )


def test_artifact_checks_on_other_targets_are_not_evidence() -> None:
    # #124 review: learner-chosen params can point a check at a harmless command
    # or an unrelated name; such passes must not stand in for the item's targets.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    elsewhere = [
        STARTED,
        _check(2, "dns.name_resolves", passed=True, params={"name": "example.org"}),
        _check(3, "dns.command_exit", passed=True, params={"argv": ["true"]}),
    ]
    with pytest.raises(DrillJudgeError, match="no artifact.checked"):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=elsewhere)
    with pytest.raises(
        DrillJudgeError, match="not run since the last lab change: dns.command_exit"
    ):
        _judge(
            item,
            answer,
            FakeLLM({"missing": []}),
            artifact_events=[*elsewhere, _check(4, "dns.name_resolves", passed=True)],
        )


def test_artifact_judgment_requires_every_check_the_item_expects() -> None:
    # #124 review: an item's artifact declares all its checks; submitting with
    # only some of them leaves the rest unverified and must not be judged.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=5, artifact_id="art_1")
    events = [STARTED, _check(2, "dns.name_resolves", passed=True)]
    with pytest.raises(
        DrillJudgeError, match="not run since the last lab change: dns.command_exit"
    ):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=events)


def test_artifact_checks_after_the_last_learner_command_are_the_only_ones_trusted() -> None:
    # #124 review: a check run before the learner's last command may describe a
    # lab that command changed, so it does not count and submission is rejected.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-fix-resolver-lab")
    answer = _event(item.id, "artifact", position=9, artifact_id="art_1")
    stale = [
        STARTED,
        _check(2, "dns.name_resolves", passed=True),
        _check(3, "dns.command_exit", passed=True),
        _command(4),
    ]
    with pytest.raises(DrillJudgeError, match="no artifact.checked"):
        _judge(item, answer, FakeLLM({"missing": []}), artifact_events=stale)
    fresh = [
        STARTED,
        _command(2),
        _check(3, "dns.name_resolves", passed=True),
        _check(4, "dns.command_exit", passed=True),
    ]
    _judge(item, answer, FakeLLM({"missing": []}), artifact_events=fresh)


def test_gap_labels_must_belong_to_the_answered_item() -> None:
    # #124 review: a pack-valid label that is not on this item must not be
    # recorded, or gap scheduling targets a dimension the item never asked about.
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-resolver-text")
    answer = _event(item.id, "text", actual="I do not know")
    off_item = {
        "missing": [
            {"description": "Review resolver configuration.", "labels": ["troubleshooting"]}
        ]
    }
    with pytest.raises(DrillJudgeError, match="not labels of this item"):
        _judge(item, answer, FakeLLM(off_item))
    on_item = {
        "missing": [
            {"description": "Review resolver configuration.", "labels": ["difficulty:easy"]}
        ]
    }
    gap, _ = _judge(item, answer, FakeLLM(on_item))
    assert gap["missing"]


def test_leak_guard_rejects_partial_and_normalized_disclosure() -> None:
    expected = "`/etc/resolv.conf`: it lists up to three `nameserver` addresses."
    assert not discloses_expected(expected, ["Review resolver configuration."])
    assert not discloses_expected(expected, ["Name the file that lists the nameservers."])
    assert discloses_expected(expected, ["Review /etc/resolv.conf"])  # a fragment
    assert discloses_expected(expected, ["Review **/ETC/RESOLV.CONF**."])  # case + markdown
    assert discloses_expected(expected, ["Review the file", "`/etc/resolv.conf`"])  # split
    assert discloses_expected("A", ["The answer is *a* record."])  # the whole answer, normalized
    assert not discloses_expected("A", ["Review the concept in the item labels."])
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-resolver-text")
    answer = _event(item.id, "text", actual="I do not know")
    leak = {"missing": [{"description": "Review /etc/resolv.conf", "labels": []}]}
    with pytest.raises(DrillJudgeError, match="disclose"):
        _judge(item, answer, FakeLLM(leak))


def test_judgment_id_is_validated_and_recording_is_race_safe() -> None:
    # #124: a learner-chosen answer id equal to another answer's judgment id
    # is not that judgment; recording then surfaces the conflict, never "complete".
    schemas = ContractSchemas.load()
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    item = next(i for i in pack_items(pack) if i.id == "dns-record-choice")
    answer = _event(item.id, "choice", actual="AAAA")
    squat = EventV2(
        id=judgment_id_of(answer.id),
        type="drill.answered",
        actor="learner",
        user_id="usr_1",
        session_id="ses_1",
        ws_id="ws_1",
        payload={"item_id": item.id, "answer_mode": "choice", "actual": "A"},
    )
    gap, _ = compute_gap(answer=answer, item=item, pack=pack, schemas=schemas)
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        tx.append(squat)
        assert stored_judgment(tx, answer.id) is None
        with pytest.raises(EventIdConflictError):
            record_judgment(
                tx, answer=answer, gap=gap, pack=pack, harness_version="0.2.0", llm_provenance=None
            )
    other = _event(item.id, "choice", actual="AAAA")
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        first = record_judgment(
            tx, answer=other, gap=gap, pack=pack, harness_version="0.2.0", llm_provenance=None
        )
        again = record_judgment(
            tx,
            answer=other,
            gap={"missing": []},
            pack=pack,
            harness_version="0.2.0",
            llm_provenance=None,
        )
    assert again == first
    with store.transaction() as tx:
        assert stored_judgment(tx, other.id) == first


def test_claim_judgment_is_single_flight_per_answer() -> None:
    claims = InMemoryClaimStore()
    with claim_judgment(claims, "a") as first:
        assert first
        with claim_judgment(claims, "a") as second, claim_judgment(claims, "b") as other:
            assert not second and other
    with claim_judgment(claims, "a") as after:
        assert after


def test_claim_judgment_lease_expires_after_a_crashed_holder() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    claims = InMemoryClaimStore(clock=lambda: now[0])
    assert claims.try_claim("judge:a", "crashed-worker", timedelta(minutes=10))
    with claim_judgment(claims, "a") as blocked:
        assert not blocked
    now[0] += timedelta(minutes=11)
    with claim_judgment(claims, "a") as retry:
        assert retry
