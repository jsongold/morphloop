"""Runtime pre-generation (#64): fake LLM, fake lab, in-memory store."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.v2.routes.notebook_generate import router
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import CheckObservation, DomainAdapterRegistry
from harness.core.generator.runtime import (
    ORIGIN_GENERATED,
    GenerateRequest,
    LabValidation,
    PreGenerator,
)
from harness.core.generator.validate import CandidateValidator
from harness.core.pack.v2.importer import LLMRole, import_pack_v2
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    JsonObject,
    LabRuntime,
    LLMError,
    ResourceLimits,
)
from harness.testing.fakes import (
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeLLMProvider,
)
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

_PACK = import_pack_v2(
    Path(__file__).resolve().parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack",
    artifact_types=PACK_ARTIFACT_TYPES,
)
ROLE = LLMRole(
    role="generator",
    model="fake-model",
    temperature=None,
    max_tokens=100,
    prompt="llm/generator.md",
    prompt_text="Write one item.",
    output_schema=None,
)
PACK = dataclasses.replace(_PACK, llm_roles={**_PACK.llm_roles, "generator": ROLE})
SCHEMAS = ContractSchemas.load()
FIX_ARGV = ["touch", "/fixed"]


class NameResolves:
    """Passes when ``getent <name>`` exits 0 in the lab."""

    def validate_params(self, params: JsonObject) -> None:
        pass

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        request = ExecRequest(
            argv=("getent", str(params["name"])),
            timeout_seconds=5,
            max_output_bytes=4096,
            env={},
            workdir=None,
        )
        return CheckObservation(
            passed=lab.exec(lab_instance_id, request).exit_code == 0, observed={}
        )


def adapters() -> DomainAdapterRegistry:
    registry = DomainAdapterRegistry()
    limits = ResourceLimits(cpus=0.5, memory_bytes=2**28, pids=128, lifetime_seconds=3600)
    registry.register(
        FakeDomainAdapter(
            adapter_id="dns",
            version="0.1.2",
            fixtures={"broken-resolver": FakeFixtureProvider(limits=limits, network="isolated")},
            checks={"name_resolves": NameResolves()},
            tools={},
        )
    )
    return registry


def lab(*, broken_passes: bool = False) -> FakeLabRuntime:
    """``getent`` fails until ``FIX_ARGV`` ran in that lab (or always passes)."""
    fixed: set[str] = set()

    def handler(lab_id: str, request: ExecRequest) -> ExecResult:
        if list(request.argv) == FIX_ARGV:
            fixed.add(lab_id)
        ok = broken_passes or lab_id in fixed or request.argv[0] != "getent"
        return ExecResult(
            exit_code=0 if ok else 1,
            stdout=b"",
            stderr=b"",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    return FakeLabRuntime(handler)


def iter_ids() -> Any:
    ids = iter(range(1, 100))
    return lambda: f"lab_{next(ids):032x}"


MEMO = [{"text": "I confused NXDOMAIN with SERVFAIL"}]


def build(
    *outputs: Any, runtime: FakeLabRuntime | None = None
) -> tuple[PreGenerator, InMemoryGeneratedDocumentStore, FakeLLMProvider]:
    llm = FakeLLMProvider(list(outputs))
    store = InMemoryGeneratedDocumentStore()
    validator = CandidateValidator(
        schemas=SCHEMAS, adapters=adapters(), lab=runtime or lab(), new_lab_id=iter_ids()
    )
    generator = PreGenerator(
        pack=PACK,
        llm=llm,
        provider="fake",
        schemas=SCHEMAS,
        store=store,
        lab=LabValidation(validator=validator, solution_step_timeout_seconds=30),
    )
    return generator, store, llm


def choice_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": "ignored",
        "question": "Which status means the name does not exist?",
        "expected": "NXDOMAIN",
        "answer_mode": "choice",
        "choices": ["NXDOMAIN", "SERVFAIL"],
        "labels": ["topic:network.dns.records", "concept"],
    }
    return {**item, **overrides}


def lab_candidate() -> dict[str, Any]:
    return {
        "title": "A name does not resolve",
        "mission": "Find where name resolution breaks and fix it.",
        "fixtures": [
            {"fixture_id": "dns.broken-resolver", "params": [{"name": "fault", "value": "x"}]}
        ],
        "checks": [
            {"check_id": "dns.name_resolves", "params": [{"name": "name", "value": "api.internal"}]}
        ],
        "hints": [],
        "reference_solution": {"explanation": "Fix it.", "sandbox_steps": [{"argv": FIX_ARGV}]},
    }


def test_stores_a_valid_drill_item_with_origin_generated() -> None:
    generator, store, llm = build({"item": choice_item(), "lab": None})
    [doc] = generator.generate(GenerateRequest(resource="drill", memo_entries=MEMO))
    assert doc.id != "ignored"
    assert doc.labels[-1] == ORIGIN_GENERATED
    assert store.list("drill") == [doc]
    assert (
        SCHEMAS.errors(doc.body, "https://morphloop.dev/contracts/schemas/pack/v2/drill-item.json")
        == []
    )
    assert doc.provenance["pack_hash"] == PACK.pack_hash
    assert llm.requests[0].role == "generator"
    assert llm.requests[0].llm.model == "fake-model"
    assert llm.requests[0].llm.generation_parameters == {"max_tokens": 100}
    assert llm.requests[0].messages[0].content == "Write one item."
    assert "NXDOMAIN with SERVFAIL" in llm.requests[0].messages[1].content


def test_stores_a_textbook_doc() -> None:
    doc_output = {
        "id": "x",
        "title": "Answers",
        "labels": ["topic:network.dns.records"],
        "blocks": [{"id": "summary", "body": "NXDOMAIN means no such name.", "labels": []}],
    }
    generator, store, _ = build(doc_output)
    [doc] = generator.generate(GenerateRequest(resource="textbook", memo_entries=MEMO))
    assert store.list("textbook") == [doc]
    assert ORIGIN_GENERATED in doc.labels


@pytest.mark.parametrize(
    "item",
    [
        choice_item(labels=["sys:holdout"]),
        choice_item(labels=["origin:pack"]),
        choice_item(labels=["not-in-vocabulary"]),
        choice_item(expected="REFUSED"),
        choice_item(question=1),
        choice_item(answer_mode="artifact", choices=None, artifact_ref="missing"),
    ],
)
def test_rejected_candidates_store_nothing(item: dict[str, Any]) -> None:
    item = {k: v for k, v in item.items() if v is not None}
    generator, store, _ = build({"item": item, "lab": None})
    assert generator.generate(GenerateRequest(resource="drill", memo_entries=MEMO)) == ()
    assert store.list("drill") == []


def test_llm_failure_stores_nothing() -> None:
    generator, store, _ = build(LLMError("down"))
    assert generator.generate(GenerateRequest(resource="drill", memo_entries=[])) == ()
    assert store.list("drill") == []


def artifact_item() -> dict[str, Any]:
    item = choice_item(answer_mode="artifact", artifact_ref="dns-broken-resolver-lab")
    del item["choices"]
    return item


def test_lab_variant_is_validated_in_the_lab_and_stored_first() -> None:
    runtime = lab()
    generator, store, _ = build({"item": artifact_item(), "lab": lab_candidate()}, runtime=runtime)
    artifact, item = generator.generate(GenerateRequest(resource="drill", memo_entries=MEMO))
    assert item.body["artifact_ref"] == artifact.id
    assert artifact.body["spec"]["environment"]["params"] == {"fault": "x"}  # type: ignore[index]
    assert "reference_solution" not in artifact.body
    assert [s["step"] for s in artifact.provenance["validation"]][
        -1
    ] == "checks_pass_after_solution"  # type: ignore[index, union-attr]
    assert store.list("artifact") == [artifact]
    assert runtime.labs == {}


def test_lab_variant_already_solved_is_rejected() -> None:
    generator, store, _ = build(
        {"item": artifact_item(), "lab": lab_candidate()}, runtime=lab(broken_passes=True)
    )
    assert generator.generate(GenerateRequest(resource="drill", memo_entries=MEMO)) == ()
    assert store.list("artifact") == [] and store.list("drill") == []


def test_post_generate_runs_in_the_background() -> None:
    llm = FakeLLMProvider([{"item": choice_item(), "lab": None}])
    store = InMemoryGeneratedDocumentStore()
    app = FastAPI()
    app.include_router(router, prefix="/v2")
    app.state.pack_v2 = _PACK  # no generator role
    app.state.generated_documents = store
    app.state.llm = llm
    client = TestClient(app)
    assert client.post("/v2/notebook/generate", json={"resource": "drill"}).status_code == 503
    app.state.pack_v2 = PACK
    response = client.post(
        "/v2/notebook/generate", json={"resource": "drill", "memo_entries": MEMO}
    )
    assert response.status_code == 202
    assert len(store.list("drill")) == 1


def test_lab_variant_without_lab_validation_is_rejected() -> None:
    llm = FakeLLMProvider([{"item": artifact_item(), "lab": lab_candidate()}])
    store = InMemoryGeneratedDocumentStore()
    generator = PreGenerator(pack=PACK, llm=llm, provider="fake", schemas=SCHEMAS, store=store)
    assert generator.generate(GenerateRequest(resource="drill", memo_entries=[])) == ()
