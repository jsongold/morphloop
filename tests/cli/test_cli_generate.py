"""Tests for the ``authoring`` generation pipeline (harness.cli.generate).

The whole path runs against fakes: a scripted LLM returning a canned
candidate, the contract DNS pack in memory, a fake lab whose name resolution
starts broken and works once the reference solution has run, and a writer that
collects what would be written instead of touching a disk.

What they pin down: the happy path writes exactly the four expected files plus
the updated manifest index, and the resulting pack still imports (AC-J1,
AC-J7); a candidate that fails any validation step writes nothing (AC-J2); a
rejected candidate is summarized in the record and another one is asked for;
and an existing Definition is never rewritten (AC-J3).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from cli_pack_fixture import (
    FIX_ARGV,
    LOCATION,
    SCHEMAS,
    SERVICE_NAME,
    TEMPLATE_ID,
    Files,
    MemoryPackWriter,
    ResolverLab,
    adapters,
    importer_factory,
    load,
    pack_files,
    pack_source,
)

from harness.cli.errors import CommandError
from harness.cli.generate import ActivityGenerator, GenerateRequest, GenerateResult
from harness.core.pack import document_hash
from harness.core.ports import ExecRequest, ExecResult
from harness.testing.fakes import FakeLabRuntime, FakeLLMProvider

ACTIVITY_ID = "gen-dns-nameserver-001"
ACTIVITY_PATH = f"activities/{ACTIVITY_ID}.json"
SOLUTION_PATH = f"activities/{ACTIVITY_ID}.solution.json"
RECORD_PATH = f"activities/{ACTIVITY_ID}.generation.json"
ENVIRONMENT_PATH = f"environments/{ACTIVITY_ID}.json"
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

ExecHandler = Callable[[str, ExecRequest], ExecResult]


def candidate() -> dict[str, Any]:
    return {
        "title": "api.internal cannot be reached by name",
        "mission": (
            f"`curl http://{SERVICE_NAME}/health` fails from this host although the service "
            "is healthy. Find where name resolution breaks and fix it."
        ),
        "fixtures": [
            {
                "fixture_id": "dns.broken-resolver",
                "params": [
                    {"name": "fault", "value": "wrong_nameserver"},
                    {"name": "service_name", "value": SERVICE_NAME},
                ],
            }
        ],
        "checks": [
            {"check_id": "dns.name_resolves", "params": [{"name": "name", "value": SERVICE_NAME}]}
        ],
        "hints": ["Ask DNS directly and read which server answered."],
        "reference_solution": {
            "explanation": "The stub resolver points at an address where no DNS server runs.",
            "sandbox_steps": [{"argv": list(FIX_ARGV)}],
        },
    }


def _lab_ids() -> Callable[[], str]:
    counter = iter(range(1, 1000))
    return lambda: f"lab_{next(counter):04d}"


class Harness:
    """One generator wired to fakes, plus the pack it works on."""

    def __init__(
        self,
        script: list[Any],
        *,
        files: Files | None = None,
        exec_handler: ExecHandler | None = None,
    ) -> None:
        self.files = pack_files() if files is None else dict(files)
        self.llm = FakeLLMProvider(script)
        self.lab = FakeLabRuntime(ResolverLab() if exec_handler is None else exec_handler)
        self.writer = MemoryPackWriter(self.files)
        self.generator = ActivityGenerator(
            source=pack_source(self.files),
            writer=self.writer,
            schemas=SCHEMAS,
            adapters=adapters(),
            llm=self.llm,
            lab=self.lab,
            importer_for=importer_factory(adapters()),
            now=lambda: NOW,
            new_lab_id=_lab_ids(),
        )

    def run(self, **kwargs: Any) -> GenerateResult:
        return self.generator.run(
            GenerateRequest(location=LOCATION, template_id=TEMPLATE_ID, **kwargs)
        )

    def written(self) -> Files:
        return {**self.files, **self.writer.written}


def _always_ok(lab_instance_id: str, request: ExecRequest) -> ExecResult:
    return ExecResult(
        exit_code=0,
        stdout=b"",
        stderr=b"",
        timed_out=False,
        stdout_truncated=False,
        stderr_truncated=False,
    )


# --- happy path -------------------------------------------------------------


def test_writes_the_definition_files_the_record_and_the_manifest_entry() -> None:
    harness = Harness([candidate()])

    result = harness.run(activity_id=ACTIVITY_ID)

    assert result.activity_id == ACTIVITY_ID
    assert result.attempts == 1
    assert result.rejected == ()
    assert set(result.written) == {ACTIVITY_PATH, SOLUTION_PATH, RECORD_PATH, ENVIRONMENT_PATH}
    assert set(harness.writer.written) == set(result.written) | {"manifest.json"}

    manifest = load(harness.writer.written, "manifest.json")
    assert manifest["files"][ACTIVITY_PATH] == {"kind": "activity"}
    assert manifest["files"][SOLUTION_PATH] == {"kind": "reference_solution"}
    assert manifest["files"][RECORD_PATH] == {"kind": "generation_record"}
    assert manifest["files"][ENVIRONMENT_PATH] == {"kind": "environment"}
    assert list(manifest["files"]) == sorted(manifest["files"])


def test_the_activity_merges_the_template_with_what_the_generator_decided() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    activity = load(harness.writer.written, ACTIVITY_PATH)
    template = load(harness.files, f"templates/{TEMPLATE_ID}.json")

    assert activity["id"] == ACTIVITY_ID
    assert activity["title"] == candidate()["title"]
    assert activity["instructions"]["mission"] == candidate()["mission"]
    assert activity["hints"] == candidate()["hints"]
    assert activity["origin"] == {
        "type": "generated",
        "template_id": TEMPLATE_ID,
        "timing": "authoring",
    }
    for field in ("activity_type", "skills", "difficulty", "evaluator", "tools", "remediation"):
        assert activity[field] == template[field], field
    assert activity["checks"] == [{"check": "dns.name_resolves", "params": {"name": SERVICE_NAME}}]
    assert activity["environment"] == f"{ACTIVITY_ID}.env"


def test_the_environment_pins_the_template_image_and_the_candidate_params() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    environment = load(harness.writer.written, ENVIRONMENT_PATH)
    template = load(harness.files, f"templates/{TEMPLATE_ID}.json")

    assert environment["id"] == f"{ACTIVITY_ID}.env"
    assert environment["fixture"] == "dns.broken-resolver"
    assert environment["image"] == template["image"]
    assert environment["params"] == {"fault": "wrong_nameserver", "service_name": SERVICE_NAME}


def test_the_reference_solution_is_a_separate_document_bound_by_hash() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    activity = load(harness.writer.written, ACTIVITY_PATH)
    solution = load(harness.writer.written, SOLUTION_PATH)

    assert "explanation" not in json.dumps(activity)
    assert activity["reference_solution"] == {
        "path": SOLUTION_PATH,
        "content_hash": document_hash(solution),
    }
    assert solution["activity_id"] == ACTIVITY_ID
    assert solution["apply"] == [{"argv": list(FIX_ARGV), "timeout_seconds": 30}]


def test_the_record_states_the_generator_the_hashes_and_the_steps_that_ran() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    record = load(harness.writer.written, RECORD_PATH)
    manifest = load(harness.files, "manifest.json")
    template = load(harness.files, f"templates/{TEMPLATE_ID}.json")

    assert record["timing"] == "authoring"
    assert record["generated_at"] == "2026-09-23T12:00:00Z"
    assert record["template"] == {
        "template_id": TEMPLATE_ID,
        "template_hash": document_hash(template),
    }
    assert record["generator"] == {
        "implementation": manifest["registry"]["generator"]["implementation"],
        "llm": manifest["registry"]["generator"]["llm"],
    }
    assert [step["step"] for step in record["validation"]["steps"]] == [
        "schema",
        "adapter_references",
        "checks_fail_in_broken_state",
        "checks_pass_after_solution",
    ]
    for step in record["validation"]["steps"]:
        assert step["outcome"] == "passed"
        assert (step["lab"] is not None) == step["step"].startswith("checks_")
    assert record["rejected_candidates"] == []
    for output in record["outputs"]:
        document = load(harness.writer.written, output["path"])
        assert output["definition_id"] == document["id"]
        assert output["definition_hash"] == document_hash(document)
    assert {o["kind"] for o in record["outputs"]} == {
        "activity",
        "environment",
        "reference_solution",
    }


def test_each_lab_phase_gets_its_own_instance_and_all_are_destroyed() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    used = [lab_id for lab_id, _ in harness.lab.exec_calls]
    assert used[0] != used[-1], "the two phases must not share a lab instance"
    assert harness.lab.labs == {}, "every validation lab is destroyed"


def test_the_generated_pack_imports() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    importer = importer_factory(adapters())(pack_source(harness.written()))
    result = importer.import_pack(LOCATION)

    assert result.created


def test_the_automatic_id_is_the_next_free_suffix() -> None:
    harness = Harness([candidate()])

    result = harness.run()

    assert result.activity_id == f"gen-{TEMPLATE_ID}-001"


def test_the_generator_is_told_the_template_the_items_and_what_exists() -> None:
    harness = Harness([candidate()])
    harness.run(activity_id=ACTIVITY_ID)

    request = harness.llm.requests[0]
    assert request.role == "generator"
    assert request.output_schema_id.endswith("generator.activity_candidate/1.json")
    assert "activity-generation" in request.messages[0].content
    context = json.loads(request.messages[1].content)
    assert context["template"]["id"] == TEMPLATE_ID
    assert {item["item_id"] for item in context["adapter_items"]} == {
        "dns.broken-resolver",
        "dns.name_resolves",
        "dns.command_exit",
    }
    assert [a["id"] for a in context["existing_activities"]] == ["gen-dns-search-domain-001"]


# --- validation: nothing is written ----------------------------------------


def _rejects(script: list[Any], *, exec_handler: ExecHandler | None = None) -> str:
    harness = Harness(script, exec_handler=exec_handler)
    with pytest.raises(CommandError) as excinfo:
        harness.run(activity_id=ACTIVITY_ID, max_attempts=1)
    assert harness.writer.written == {}, "a rejected candidate writes nothing"
    return str(excinfo.value)


def test_a_schema_invalid_candidate_writes_nothing() -> None:
    broken = candidate()
    del broken["reference_solution"]

    assert "[schema]" in _rejects([broken])


def test_a_check_outside_the_template_writes_nothing() -> None:
    broken = candidate()
    broken["checks"] = [{"check_id": "dns.http_status", "params": []}]

    message = _rejects([broken])

    assert "[adapter_references]" in message
    assert "allowed_checks" in message


def test_params_the_adapter_rejects_write_nothing() -> None:
    broken = candidate()
    broken["fixtures"][0]["params"] = [{"name": "service_name", "value": SERVICE_NAME}]

    message = _rejects([broken])

    assert "[adapter_references]" in message
    assert "fault" in message


def test_an_activity_that_already_passes_in_the_broken_state_writes_nothing() -> None:
    message = _rejects([candidate()], exec_handler=_always_ok)

    assert "[checks_fail_in_broken_state]" in message
    assert "solved before the learner starts" in message


def test_a_solution_that_does_not_fix_the_lab_writes_nothing() -> None:
    broken = candidate()
    broken["reference_solution"]["sandbox_steps"] = [{"argv": ["true"]}]

    message = _rejects([broken])

    assert "[checks_pass_after_solution]" in message
    assert "dns.name_resolves" in message


def test_a_rejected_candidate_is_summarized_and_another_one_is_asked_for() -> None:
    first = candidate()
    first["checks"] = [{"check_id": "dns.http_status", "params": []}]
    harness = Harness([first, candidate()])

    result = harness.run(activity_id=ACTIVITY_ID)

    assert result.attempts == 2
    assert [r.attempt for r in result.rejected] == [1]
    record = load(harness.writer.written, RECORD_PATH)
    entry = record["rejected_candidates"][0]
    assert len(record["rejected_candidates"]) == 1
    assert entry["attempt"] == 1
    assert entry["failed_step"] == "adapter_references"
    assert "dns.http_status" in entry["reason"]
    assert candidate()["reference_solution"]["explanation"] not in entry["reason"]
    assert "rejected" in harness.llm.requests[1].messages[-1].content


def test_a_pack_that_does_not_import_is_refused() -> None:
    files = pack_files()
    manifest = copy.deepcopy(load(files, "manifest.json"))
    del manifest["files"]["skills/network.dns.resolution.json"]
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    harness = Harness([candidate()], files=files)

    with pytest.raises(CommandError) as excinfo:
        harness.run(activity_id=ACTIVITY_ID)

    assert harness.writer.written == {}
    assert "the pack does not import" in str(excinfo.value)


def test_an_unknown_template_is_refused_before_any_llm_call() -> None:
    harness = Harness([candidate()])

    with pytest.raises(CommandError) as excinfo:
        harness.generator.run(GenerateRequest(location=LOCATION, template_id="nope"))

    assert harness.llm.requests == []
    assert "no activity_template 'nope'" in str(excinfo.value)


# --- immutability (AC-J3) ---------------------------------------------------


def test_an_existing_definition_is_never_rewritten() -> None:
    first = Harness([candidate()])
    first.run(activity_id=ACTIVITY_ID)

    again = Harness([candidate()], files=first.written())
    with pytest.raises(CommandError) as excinfo:
        again.run(activity_id=ACTIVITY_ID)

    assert again.writer.written == {}
    assert again.llm.requests == []
    assert "never rewritten" in str(excinfo.value)


def test_a_second_run_writes_a_new_id_instead() -> None:
    first = Harness([candidate()])
    first.run()

    again = Harness([candidate()], files=first.written())
    result = again.run()

    assert result.activity_id == f"gen-{TEMPLATE_ID}-002"
    assert set(again.writer.written) & set(first.writer.written) == {"manifest.json"}


# --- visualizations bound to lab values --------------------------------------

VIZ_PATH = "visualizations/dns-resolution-flow.json"
BOUND_VIZ_ID = f"{ACTIVITY_ID}.dns-resolution-flow"
BOUND_VIZ_PATH = f"visualizations/{BOUND_VIZ_ID}.json"


def _bound_pack() -> Files:
    """The contract pack with its visualization bound to the hand-authored lab's host."""
    files = pack_files()
    generated = [p for p in files if "gen-dns-search-domain-001" in p]
    for path in generated:
        del files[path]
    manifest = load(files, "manifest.json")
    for path in generated:
        del manifest["files"][path]
    viz = load(files, VIZ_PATH)
    viz["environment_bindings"] = {"service_name": SERVICE_NAME}
    files["manifest.json"] = json.dumps(manifest).encode()
    files[VIZ_PATH] = json.dumps(viz).encode()
    return files


def test_a_bound_visualization_is_copied_with_the_generated_lab_values() -> None:
    other = candidate()
    other["fixtures"][0]["params"][1]["value"] = "ledger.internal"
    harness = Harness([other], files=_bound_pack())

    result = harness.run(activity_id=ACTIVITY_ID)

    assert BOUND_VIZ_PATH in result.written
    written = harness.written()
    activity = load(written, ACTIVITY_PATH)
    assert activity["remediation"]["visualizations"] == [BOUND_VIZ_ID]
    copy = load(written, BOUND_VIZ_PATH)
    assert copy["environment_bindings"] == {"service_name": "ledger.internal"}
    text = json.dumps(copy)
    assert SERVICE_NAME not in text
    assert "ledger.internal" in text
    record = load(written, RECORD_PATH)
    assert {"kind": "visualization", "path": BOUND_VIZ_PATH}.items() <= next(
        o for o in record["outputs"] if o["kind"] == "visualization"
    ).items()
    assert importer_factory(adapters())(pack_source(written)).import_pack(LOCATION).created
