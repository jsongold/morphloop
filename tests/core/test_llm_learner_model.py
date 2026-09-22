"""Tests for the v0.1 LLM learner model (harness.core.learner_model).

The model is resolved from an imported contract DNS pack (selection + prompt read
from the projection) and driven by the fake LLM provider. Output validation is the
point: schema, evidence ids subset of the evidence given, mastery cap (AC-E4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import CheckObservation, DomainAdapterRegistry
from harness.core.learner_model import (
    EvidenceRecord,
    LearnerModel,
    LearnerModelInput,
    LearnerModelOutputError,
    SkillState,
)
from harness.core.learner_model.llm import OUTPUT_SCHEMA_ID, ContextBudgetError, LLMLearnerModel
from harness.core.learner_model.resolve import load_learner_model
from harness.core.pack import PackCatalog, PackImporter
from harness.core.ports import JsonObject, LabRuntime, LLMOutputError, ResourceLimits
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.contracts import validate
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLLMProvider,
    FakeTerminalTool,
    InMemoryEventStore,
    InMemoryPackSource,
)

DNS_PACK = Path(__file__).resolve().parent.parent / "contracts/fixtures/pack/valid/dns-pack"
SCHEMAS = ContractSchemas.load()
SKILL = "network.dns.resolution"


class _AnyParamsCheck:
    def validate_params(self, params: JsonObject) -> None:
        pass

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        return CheckObservation(passed=True, observed={})


def _model(llm: FakeLLMProvider) -> tuple[LearnerModel, JsonObject]:
    files = {p.relative_to(DNS_PACK).as_posix(): p.read_bytes() for p in DNS_PACK.rglob("*.*")}
    adapters = DomainAdapterRegistry()
    limits = ResourceLimits(cpus=1, memory_bytes=2**28, pids=64, lifetime_seconds=60)
    adapters.register(
        FakeDomainAdapter(
            adapter_id="dns",
            version="0.1.0",
            fixtures={"broken-resolver": FakeFixtureProvider(limits=limits, network="none")},
            checks={
                "name_resolves": _AnyParamsCheck(),
                "command_exit": FakeCommandExitCheck(timeout_seconds=5, max_output_bytes=1024),
            },
            tools={"terminal": FakeTerminalTool()},
        )
    )
    store = InMemoryEventStore()
    registry = v01_algorithm_registry()
    ref = (
        PackImporter(
            source=InMemoryPackSource({"dns": files}),
            store=store,
            schemas=SCHEMAS,
            adapters=adapters,
            algorithms=registry,
        )
        .import_pack("dns")
        .ref
    )
    catalog = PackCatalog(store)
    model = load_learner_model(
        catalog=catalog, pack=ref, registry=registry, llm=llm, schemas=SCHEMAS
    )
    return model, catalog.get_definition(ref, "skill", SKILL).document


def _evidence(evidence_id: str, signal: str = "positive") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evaluation_id="evl_01",
        skill_id=SKILL,
        signal="positive" if signal == "positive" else "negative",
        strength=0.7,
        dimension="diagnose",
        rationale="Checked resolv.conf before editing it.",
        supporting_event_ids=("evt_01", "evt_02"),
    )


def _input(skill: JsonObject, previous: SkillState | None = None) -> LearnerModelInput:
    return LearnerModelInput(
        pack_id="software-engineering",
        skill_id=SKILL,
        skill_definition=skill,
        previous=previous,
        evidence=(_evidence("ev_01"), _evidence("ev_02", "negative")),
    )


def _output(**overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "next": {"mastery_probability": 0.55, "uncertainty": 0.3, "hint_dependency": 0.1},
        "evidence_ids": ["ev_01"],
        "rationale": "Diagnosed the resolver without hints.",
        "predicted_success_probability": 0.6,
    }
    out.update(overrides)
    return out


def _keys_named(node: Any, name: str) -> list[Any]:
    if isinstance(node, dict):
        found = [node[name]] if name in node else []
        return found + [x for v in node.values() for x in _keys_named(v, name)]
    if isinstance(node, list):
        return [x for v in node for x in _keys_named(v, name)]
    return []


def test_update_builds_the_request_from_the_pack_and_returns_a_valid_payload() -> None:
    llm = FakeLLMProvider([_output()])
    model, skill = _model(llm)
    previous = SkillState(mastery_probability=0.4, uncertainty=0.5)
    update = model.update(_input(skill, previous))

    (request,) = llm.requests
    assert request.role == "learner_model"
    assert request.llm.prompt_id == "learner-model-update"
    assert request.llm.model == "claude-sonnet-4-5-20250929"
    assert request.output_schema_id == OUTPUT_SCHEMA_ID
    assert not _keys_named(request.output_schema, "$ref")
    assert request.output_schema["required"] == [
        "next",
        "evidence_ids",
        "rationale",
        "predicted_success_probability",
    ]
    system, user = request.messages
    assert system.role == "system" and system.content.startswith("# learner-model-update")
    context = json.loads(user.content)
    assert [e["evidence_id"] for e in context["evidence"]] == ["ev_01", "ev_02"]
    assert context["previous_state"] == {"mastery_probability": 0.4, "uncertainty": 0.5}
    assert context["skill"]["id"] == SKILL

    assert update.evidence_ids == ("ev_01",)
    assert update.previous == previous
    assert update.next.mastery_probability == 0.55
    assert update.provenance == request.llm
    validate(update.to_payload(), "schemas/events/payloads/learner_skill.updated/1.json")


def test_evidence_id_not_given_in_the_call_is_rejected() -> None:
    llm = FakeLLMProvider([_output(evidence_ids=["ev_01", "ev_99"])])
    model, skill = _model(llm)
    with pytest.raises(LearnerModelOutputError, match="ev_99"):
        model.update(_input(skill))


@pytest.mark.parametrize(
    "output",
    [
        {k: v for k, v in _output().items() if k != "predicted_success_probability"},
        {k: v for k, v in _output().items() if k != "rationale"},
        _output(evidence_ids=[]),
        _output(predicted_success_probability=1.2),
        _output(next={"mastery_probability": 0.5}),
        _output(extra="field"),
    ],
    ids=["no-prediction", "no-rationale", "no-evidence", "prob-range", "bad-state", "extra"],
)
def test_schema_invalid_output_is_rejected(output: dict[str, Any]) -> None:
    model, skill = _model(FakeLLMProvider([output]))
    with pytest.raises(LearnerModelOutputError, match="schema"):
        model.update(_input(skill))


def test_mastery_move_over_the_pack_cap_is_rejected() -> None:
    llm = FakeLLMProvider([_output(next={"mastery_probability": 0.95, "uncertainty": 0.2})])
    model, skill = _model(llm)
    previous = SkillState(mastery_probability=0.4, uncertainty=0.5)
    with pytest.raises(LearnerModelOutputError, match="max_mastery_delta"):
        model.update(_input(skill, previous))


def test_provider_output_error_propagates() -> None:
    model, skill = _model(FakeLLMProvider([LLMOutputError("refused")]))
    with pytest.raises(LLMOutputError):
        model.update(_input(skill))


def test_request_over_the_context_budget_is_refused_before_calling_the_llm() -> None:
    llm = FakeLLMProvider([_output()])
    model, skill = _model(llm)
    assert isinstance(model, LLMLearnerModel)
    big = LearnerModelInput(
        pack_id="software-engineering",
        skill_id=SKILL,
        skill_definition=skill,
        previous=None,
        evidence=tuple(_evidence(f"ev_{i:04d}") for i in range(400)),
    )
    with pytest.raises(ContextBudgetError):
        model.update(big)
    assert llm.requests == []


def test_input_invariants() -> None:
    _, skill = _model(FakeLLMProvider())
    with pytest.raises(ValueError):
        LearnerModelInput(
            pack_id="p", skill_id=SKILL, skill_definition=skill, previous=None, evidence=()
        )
    with pytest.raises(ValueError):
        LearnerModelInput(
            pack_id="p",
            skill_id="other.skill",
            skill_definition=skill,
            previous=None,
            evidence=(_evidence("ev_01"),),
        )
    with pytest.raises(ValueError):
        SkillState(mastery_probability=1.2, uncertainty=0.1)
