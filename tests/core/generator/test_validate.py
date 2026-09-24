"""Tests for harness.core.generator.validate: one candidate, fakes only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    AdapterParamsError,
    CheckObservation,
    DomainAdapterRegistry,
)
from harness.core.generator.validate import (
    ADAPTER_STEP,
    BROKEN_STEP,
    FIXED_STEP,
    SCHEMA_STEP,
    Candidate,
    CandidateValidator,
    Paths,
    Rejected,
)
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    JsonObject,
    LabRuntime,
    LLMProvenance,
    ResourceLimits,
)
from harness.testing.fakes import (
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeTerminalTool,
)

DNS_PACK = Path(__file__).resolve().parents[2] / "contracts/fixtures/pack/valid/dns-pack"
TEMPLATE = json.loads((DNS_PACK / "templates/diagnose-dns-failure.json").read_text())
MANIFEST = json.loads((DNS_PACK / "manifest.json").read_text())
SCHEMAS = ContractSchemas.load()
FIX_ARGV = ["touch", "/fixed"]
ACTIVITY_ID = "gen-x-001"
PATHS = Paths(
    activity=f"activities/{ACTIVITY_ID}.json",
    environment=f"environments/{ACTIVITY_ID}.json",
    solution=f"activities/{ACTIVITY_ID}.solution.json",
    record=f"activities/{ACTIVITY_ID}.generation.json",
)
PROVENANCE = LLMProvenance(
    provider="fake", model="fake", prompt_id="p", prompt_version="1", generation_parameters={}
)


class NameResolves:
    """Passes when ``getent <name>`` exits 0 in the lab."""

    def validate_params(self, params: JsonObject) -> None:
        if not isinstance(params.get("name"), str):
            raise AdapterParamsError("params.name must be a string")

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        request = ExecRequest(
            argv=("getent", str(params["name"])),
            timeout_seconds=5,
            max_output_bytes=4096,
            env={},
            workdir=None,
        )
        passed = lab.exec(lab_instance_id, request).exit_code == 0
        return CheckObservation(passed=passed, observed={})


def adapters() -> DomainAdapterRegistry:
    registry = DomainAdapterRegistry()
    limits = ResourceLimits(cpus=0.5, memory_bytes=2**28, pids=128, lifetime_seconds=3600)
    registry.register(
        FakeDomainAdapter(
            adapter_id="dns",
            version="0.1.2",
            fixtures={"broken-resolver": FakeFixtureProvider(limits=limits, network="isolated")},
            checks={"name_resolves": NameResolves()},
            tools={"terminal": FakeTerminalTool()},
        )
    )
    return registry


def candidate(**overrides: Any) -> dict[str, Any]:
    output: dict[str, Any] = {
        "title": "A name does not resolve",
        "mission": "Find where name resolution breaks and fix it.",
        "fixtures": [
            {"fixture_id": "dns.broken-resolver", "params": [{"name": "fault", "value": "x"}]}
        ],
        "checks": [
            {
                "check_id": "dns.name_resolves",
                "params": [{"name": "name", "value": "api.internal"}],
            }
        ],
        "hints": [],
        "reference_solution": {"explanation": "Fix it.", "sandbox_steps": [{"argv": FIX_ARGV}]},
    }
    output.update(overrides)
    return output


def lab(*, broken_passes: bool = False) -> FakeLabRuntime:
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


def validate(output: dict[str, Any], runtime: FakeLabRuntime | None = None) -> Any:
    validator = CandidateValidator(
        schemas=SCHEMAS, adapters=adapters(), lab=runtime or lab(), new_lab_id=iter_ids()
    )
    return validator.validate(
        candidate=Candidate(output=output, provenance=PROVENANCE),
        manifest=MANIFEST,
        template=TEMPLATE,
        activity_id=ACTIVITY_ID,
        paths=PATHS,
        visualizations={},
    )


def iter_ids() -> Any:
    ids = iter(range(1, 100))
    return lambda: f"lab_{next(ids):032x}"


def test_accepts_a_candidate_whose_checks_fail_broken_and_pass_fixed() -> None:
    runtime = lab()
    accepted = validate(candidate(), runtime)
    assert [s["step"] for s in accepted.steps] == [
        SCHEMA_STEP,
        ADAPTER_STEP,
        BROKEN_STEP,
        FIXED_STEP,
    ]
    assert accepted.activity["reference_solution"]["path"] == PATHS.solution
    assert accepted.environment["fixture"] == "dns.broken-resolver"
    assert runtime.labs == {}  # both labs destroyed


@pytest.mark.parametrize(
    ("output", "runtime", "step"),
    [
        (candidate(title=1), None, SCHEMA_STEP),
        (
            candidate(checks=[{"check_id": "dns.command_exit", "params": []}]),
            None,
            ADAPTER_STEP,
        ),
        (
            candidate(
                fixtures=[
                    {
                        "fixture_id": "dns.broken-resolver",
                        "params": [{"name": "a", "value": 1}, {"name": "a", "value": 2}],
                    }
                ]
            ),
            None,
            ADAPTER_STEP,
        ),
        (candidate(), lab(broken_passes=True), BROKEN_STEP),
        (
            candidate(reference_solution={"explanation": "no-op", "sandbox_steps": []}),
            None,
            FIXED_STEP,
        ),
    ],
)
def test_rejects_at_the_failing_step(
    output: dict[str, Any], runtime: FakeLabRuntime | None, step: str
) -> None:
    with pytest.raises(Rejected) as excinfo:
        validate(output, runtime)
    assert excinfo.value.step == step
