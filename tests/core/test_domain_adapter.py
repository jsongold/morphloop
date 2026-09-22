"""Tests for the domain adapter contract and registry (harness.core.domain_adapter).

Covers: SemVer range matching for manifest ``domain_adapters`` (ADR-0009),
registration rules, resolution of ``'<adapter_id>.<name>'`` item ids, the
Importer-facing ``verify`` report (unregistered adapters/items, version ranges,
undeclared adapters, invalid params) against the contract DNS example pack,
running a check through the LabRuntime Port with the result validated against
``evaluation.completed``, and the fake fixture provider / terminal tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.core.domain_adapter import (
    AdapterParamsError,
    CheckObservation,
    DetectedCommand,
    DomainAdapter,
    DomainAdapterRegistry,
    ItemReference,
    SemVer,
    TerminalLaunch,
    UnregisteredItemError,
    run_check,
    version_satisfies,
)
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    ImageRef,
    JsonObject,
    LabRuntime,
    ResourceLimits,
    TerminalOpenRequest,
    TerminalSize,
)
from harness.testing.contracts import validate
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeTerminalTool,
)

DNS_PACK = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "fixtures"
    / "pack"
    / "valid"
    / "dns-pack"
)
IMAGE = ImageRef(repository="ghcr.io/morphloop/dns-lab", digest="sha256:" + "3f" * 32)
LIMITS = ResourceLimits(cpus=0.5, memory_bytes=256 * 2**20, pids=128, lifetime_seconds=3600)


class _NameResolvesCheck:
    def validate_params(self, params: JsonObject) -> None:
        if not isinstance(params.get("name"), str):
            raise AdapterParamsError("params.name must be a string")

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        return CheckObservation(passed=True, observed={"name": params["name"]})


def _dns_adapter(version: str = "0.1.3") -> FakeDomainAdapter:
    return FakeDomainAdapter(
        adapter_id="dns",
        version=version,
        fixtures={
            "broken-resolver": FakeFixtureProvider(
                limits=LIMITS, network="isolated", required_params=("fault",)
            )
        },
        checks={
            "name_resolves": _NameResolvesCheck(),
            "command_exit": FakeCommandExitCheck(timeout_seconds=10, max_output_bytes=4096),
        },
        tools={"terminal": FakeTerminalTool()},
    )


def _registry(*adapters: DomainAdapter) -> DomainAdapterRegistry:
    registry = DomainAdapterRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _pack_references(root: Path) -> tuple[dict[str, str], list[ItemReference]]:
    """What the Importer would extract from the contract DNS example pack."""
    manifest = _load(root / "manifest.json")
    refs: list[ItemReference] = []
    for rel, entry in sorted(manifest["files"].items()):
        kind = entry["kind"]
        if kind == "environment":
            doc = _load(root / rel)
            refs.append(
                ItemReference(
                    kind="fixture", item_id=doc["fixture"], params=doc["params"], source=rel
                )
            )
        elif kind == "activity":
            doc = _load(root / rel)
            for i, check in enumerate(doc["checks"]):
                refs.append(
                    ItemReference(
                        kind="check",
                        item_id=check["check"],
                        params=check["params"],
                        source=f"{rel}#/checks/{i}",
                    )
                )
            refs += [
                ItemReference(kind="tool", item_id=t, params=None, source=f"{rel}#/tools")
                for t in doc["tools"]
            ]
        elif kind == "activity_template":
            doc = _load(root / rel)
            refs += [
                ItemReference(kind="fixture", item_id=f, params=None, source=rel)
                for f in doc["allowed_fixtures"]
            ]
            refs += [
                ItemReference(kind="check", item_id=c, params=None, source=rel)
                for c in doc["allowed_checks"]
            ]
    return manifest["domain_adapters"], refs


# --- Versions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "version_range", "expected"),
    [
        ("0.1.0", ">=0.1.0,<0.2.0", True),
        ("0.1.9", ">=0.1.0,<0.2.0", True),
        ("0.2.0", ">=0.1.0,<0.2.0", False),
        ("0.0.9", ">=0.1.0,<0.2.0", False),
        ("0.2.0-rc.1", "<0.2.0", True),  # a pre-release precedes its release
        ("1.0.0+build.7", "==1.0.0", True),  # build metadata is ignored
        ("1.0.0", ">1.0.0-rc.1", True),
        ("1.0.0", "<=1.0.0", True),
        ("1.10.0", ">1.9.0", True),  # numeric, not lexical
    ],
)
def test_version_satisfies(version: str, version_range: str, expected: bool) -> None:
    assert version_satisfies(version, version_range) is expected


def test_semver_prerelease_precedence() -> None:
    ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta.2"]
    ordered += ["1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    parsed = [SemVer.parse(v) for v in ordered]
    assert sorted(reversed(parsed)) == parsed


@pytest.mark.parametrize(
    ("version", "version_range"),
    [("1", ">=1.0.0"), ("1.0.0", ">=1"), ("1.0.0", "~1.0.0"), ("1.0.0", ">=1.0.0,")],
)
def test_version_satisfies_rejects_malformed(version: str, version_range: str) -> None:
    with pytest.raises(ValueError):
        version_satisfies(version, version_range)


# --- Registration and resolution ---------------------------------------------


def test_register_rejects_duplicates_ambiguity_and_bad_versions() -> None:
    registry = _registry(_dns_adapter())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_dns_adapter())
    with pytest.raises(ValueError, match="ambiguous"):
        registry.register(FakeDomainAdapter(adapter_id="dns.extra", version="1.0.0"))
    with pytest.raises(ValueError, match="SemVer"):
        registry.register(FakeDomainAdapter(adapter_id="http", version="1"))
    with pytest.raises(ValueError, match="invalid adapter id"):
        registry.register(FakeDomainAdapter(adapter_id="HTTP", version="1.0.0"))
    with pytest.raises(ValueError, match="invalid item name"):
        registry.register(
            FakeDomainAdapter(adapter_id="http", version="1.0.0", tools={"": FakeTerminalTool()})
        )


def test_resolves_items_by_kind() -> None:
    adapter = _dns_adapter()
    registry = _registry(adapter)
    assert registry.fixture("dns.broken-resolver") is adapter.fixtures["broken-resolver"]
    assert registry.check("dns.command_exit") is adapter.checks["command_exit"]
    assert registry.tool("dns.terminal") is adapter.tools["terminal"]
    assert registry.split_item_id("dns.a.b") == ("dns", "a.b")
    assert registry.split_item_id("dns.") is None
    for resolve, item_id in [
        (registry.check, "dns.terminal"),  # registered, but as a tool
        (registry.fixture, "dns.missing"),
        (registry.tool, "http.terminal"),
        (registry.check, "dns"),
    ]:
        with pytest.raises(UnregisteredItemError):
            resolve(item_id)


# --- Importer verification ---------------------------------------------------


def test_verify_accepts_the_contract_dns_pack() -> None:
    requirements, refs = _pack_references(DNS_PACK)
    assert {r.kind for r in refs} == {"fixture", "check", "tool"}
    registry = _registry(_dns_adapter())
    assert registry.verify(requirements, refs) == []
    assert registry.adapter_versions(requirements) == {"dns": "0.1.3"}


def test_verify_reports_adapter_level_problems_once() -> None:
    requirements, refs = _pack_references(DNS_PACK)
    problems = _registry().verify(requirements, refs)
    assert [(p.code, p.subject, p.source) for p in problems] == [
        ("adapter_not_registered", "dns", None)
    ]
    problems = _registry(_dns_adapter("0.2.0")).verify(requirements, refs)
    assert [(p.code, p.subject) for p in problems] == [("version_out_of_range", "dns")]
    problems = _registry(_dns_adapter()).verify({"dns": "^0.1"}, refs)
    assert [(p.code, p.subject) for p in problems] == [("invalid_version_range", "dns")]


def test_verify_reports_item_level_problems() -> None:
    registry = _registry(_dns_adapter(), FakeDomainAdapter(adapter_id="http", version="1.0.0"))
    refs = [
        ItemReference(kind="check", item_id="dns.nope", params={}, source="a.json#/checks/0"),
        ItemReference(kind="fixture", item_id="dns.terminal", params=None, source="e.json"),
        ItemReference(kind="tool", item_id="ftp.shell", params=None, source="a.json#/tools"),
        ItemReference(kind="tool", item_id="http.terminal", params=None, source="a.json#/tools"),
        ItemReference(
            kind="check",
            item_id="dns.command_exit",
            params={"argv": ["true"]},
            source="a.json#/checks/1",
        ),
        ItemReference(kind="fixture", item_id="dns.broken-resolver", params={}, source="env.json"),
        # params=None skips validation (e.g. a Template's allowed_fixtures).
        ItemReference(kind="fixture", item_id="dns.broken-resolver", params=None, source="t"),
    ]
    problems = registry.verify({"dns": ">=0.1.0,<0.2.0"}, refs)
    assert [(p.code, p.subject, p.source) for p in problems] == [
        ("item_not_registered", "dns.nope", "a.json#/checks/0"),
        ("item_not_registered", "dns.terminal", "e.json"),
        ("item_not_registered", "ftp.shell", "a.json#/tools"),
        ("adapter_not_declared", "http.terminal", "a.json#/tools"),
        ("invalid_params", "dns.command_exit", "a.json#/checks/1"),
        ("invalid_params", "dns.broken-resolver", "env.json"),
    ]


# --- Checks, fixtures, terminal ------------------------------------------------


def _exit_with(code: int | None) -> FakeLabRuntime:
    def handler(lab_instance_id: str, request: ExecRequest) -> ExecResult:
        return ExecResult(
            exit_code=code,
            stdout=b"",
            stderr=b"",
            timed_out=code is None,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    return FakeLabRuntime(exec_handler=handler)


@pytest.mark.parametrize(("exit_code", "passed"), [(0, True), (7, False), (None, False)])
def test_run_check_executes_inside_the_lab(exit_code: int | None, passed: bool) -> None:
    registry = _registry(_dns_adapter())
    lab = _exit_with(exit_code)
    spec = registry.fixture("dns.broken-resolver").build_lab_spec(IMAGE, {"fault": "x"})
    lab.start("lab_1", spec)
    params: JsonObject = {"argv": ["curl", "-fsS", "http://api/health"], "expected_exit_code": 0}

    result = run_check(registry, "dns.command_exit", params, lab=lab, lab_instance_id="lab_1")

    assert result.check_id == "dns.command_exit"
    assert result.passed is passed
    assert [(lab_id, req.argv) for lab_id, req in lab.exec_calls] == [
        ("lab_1", ("curl", "-fsS", "http://api/health"))
    ]
    payload = {
        "evaluation_id": "evl_1",
        "evaluator": {"definition_id": "dns-diagnosis-v1", "definition_hash": "sha256:" + "0" * 64},
        "lab_instance_id": "lab_1",
        "checks": [result.to_dict()],
        "success": result.passed,
        "rationale": None,
        "provenance": None,
    }
    validate(payload, "schemas/events/payloads/evaluation.completed/1.json")


def test_fixture_provider_builds_a_deterministic_spec_with_the_pinned_image() -> None:
    fixture = _dns_adapter().fixtures["broken-resolver"]
    params: JsonObject = {"fault": "wrong_nameserver", "service_name": "api.internal"}
    spec = fixture.build_lab_spec(IMAGE, params)
    assert spec.image == IMAGE
    assert spec == fixture.build_lab_spec(IMAGE, params)  # reset rebuilds the same lab
    lab = FakeLabRuntime()
    info = lab.start("lab_1", spec)
    payload = {
        "lab_instance_id": info.lab_instance_id,
        "environment": {
            "definition_id": "dns-broken-resolver-v1",
            "definition_hash": "sha256:" + "0" * 64,
        },
        "trigger": "initial",
        "replaces_lab_instance_id": None,
        # The fixture id recorded as provenance is the environment's adapter item id.
        "provenance": {"image_digest": info.image_digest, "fixture_id": "dns.broken-resolver"},
    }
    validate(payload, "schemas/events/payloads/lab.started/1.json")


def test_terminal_tool_launch_and_command_detection() -> None:
    tool = _dns_adapter().tools["terminal"]
    launch = tool.launch()
    request = TerminalOpenRequest(
        lab_instance_id="lab_1",
        runtime_ref="fake-lab_1",
        terminal_id="term_1",
        argv=launch.argv,
        size=TerminalSize(cols=80, rows=24),
        env=launch.env,
        workdir=launch.workdir,
    )
    assert request.argv == ("/bin/sh", "-l")

    detector = tool.new_command_detector()
    assert detector.feed_input(b"dig api.int") == []
    assert list(detector.feed_input(b"ernal\rcat /etc/resolv.conf\r\r")) == [
        DetectedCommand(command="dig api.internal", cwd=None),
        DetectedCommand(command="cat /etc/resolv.conf", cwd=None),
    ]
    # A second session starts clean.
    assert list(tool.new_command_detector().feed_input(b"ls\n")) == [
        DetectedCommand(command="ls", cwd=None)
    ]


def test_value_type_invariants() -> None:
    with pytest.raises(ValueError):
        TerminalLaunch(argv=("/bin/sh -l",), env={}, workdir=None)
    with pytest.raises(ValueError):
        DetectedCommand(command="", cwd=None)
    with pytest.raises(ValueError):
        DetectedCommand(command="ls", cwd="")
