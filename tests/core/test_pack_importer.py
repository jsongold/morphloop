"""Tests for the pack Importer and the pack projection read API (harness.core.pack).

Input is the contract DNS example pack (tests/contracts/fixtures/pack/valid/dns-pack/),
served by the in-memory pack source, imported into the in-memory event store, with a
fake ``dns`` domain adapter and the v0.1 algorithm registry.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    AdapterParamsError,
    CheckObservation,
    DomainAdapterRegistry,
)
from harness.core.pack import (
    DEFINITION_PROJECTION,
    PACK_PROJECTION,
    SECRET_PROJECTION,
    PackCatalog,
    PackImporter,
    PackImportError,
    PackNotFoundError,
    PackProjectionConflictError,
    canonicalize,
    document_hash,
    learner_view_of_activity,
    pack_content_hash,
)
from harness.core.ports import JsonObject, LabRuntime, ResourceLimits
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeTerminalTool,
    InMemoryEventStore,
    InMemoryPackSource,
)

DNS_PACK = Path(__file__).resolve().parent.parent / "contracts/fixtures/pack/valid/dns-pack"
LOCATION = "packs/dns"
ACTIVITY = "activities/diagnose-dns-resolver-failure-v1.json"
SOLUTION = "activities/diagnose-dns-resolver-failure-v1.solution.json"
SCHEMAS = ContractSchemas.load()

Files = dict[str, bytes]


class _NameResolvesCheck:
    def validate_params(self, params: JsonObject) -> None:
        if not isinstance(params.get("name"), str):
            raise AdapterParamsError("params.name must be a string")

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        return CheckObservation(passed=True, observed={})


def _adapters() -> DomainAdapterRegistry:
    registry = DomainAdapterRegistry()
    limits = ResourceLimits(cpus=0.5, memory_bytes=2**28, pids=128, lifetime_seconds=3600)
    registry.register(
        FakeDomainAdapter(
            adapter_id="dns",
            version="0.1.2",
            fixtures={
                "broken-resolver": FakeFixtureProvider(
                    limits=limits, network="isolated", required_params=("fault",)
                )
            },
            checks={
                "name_resolves": _NameResolvesCheck(),
                "command_exit": FakeCommandExitCheck(timeout_seconds=10, max_output_bytes=4096),
            },
            tools={"terminal": FakeTerminalTool()},
        )
    )
    return registry


def _files() -> Files:
    return {
        p.relative_to(DNS_PACK).as_posix(): p.read_bytes()
        for p in sorted(DNS_PACK.rglob("*"))
        if p.is_file()
    }


def _json(files: Files, path: str) -> Any:
    return json.loads(files[path])


def _edit(files: Files, path: str, change: Callable[[Any], None]) -> Files:
    doc = _json(files, path)
    change(doc)
    return {**files, path: json.dumps(doc, indent=2).encode()}


def _importer(files: Files, store: InMemoryEventStore | None = None) -> PackImporter:
    return PackImporter(
        source=InMemoryPackSource({LOCATION: files}),
        store=store if store is not None else InMemoryEventStore(),
        schemas=SCHEMAS,
        adapters=_adapters(),
        algorithms=v01_algorithm_registry(),
    )


def _refused(files: Files) -> list[tuple[str, str | None]]:
    store = InMemoryEventStore()
    with pytest.raises(PackImportError) as excinfo:
        _importer(files, store).import_pack(LOCATION)
    with store.transaction() as tx:
        for name in (PACK_PROJECTION, DEFINITION_PROJECTION, SECRET_PROJECTION):
            assert tx.list_projection(name) == [], "a refused pack must write nothing"
    return [(p.code, p.path) for p in excinfo.value.problems]


# --- Happy path -------------------------------------------------------------


def test_imports_the_contract_pack_and_reads_it_back_typed() -> None:
    files = _files()
    store = InMemoryEventStore()
    result = _importer(files, store).import_pack(LOCATION)

    assert result.created
    assert result.ref.pack_id == "software-engineering"
    assert result.ref.pack_version == "0.1.0"
    assert result.ref.content_hash == pack_content_hash(files.items())

    catalog = PackCatalog(store)
    assert catalog.list_packs() == [result.ref]
    assert catalog.list_packs("other-pack") == []
    pack = catalog.get_pack(result.ref)
    assert pack.title == "Software Engineering Fundamentals"
    assert pack.domain_adapters == {"dns": ">=0.1.0,<0.2.0"}
    learner_model = pack.registry["learner_model"]
    assert learner_model.implementation == "llm-learner-model@0.1.0"
    assert learner_model.options == {"max_mastery_delta": 0.3}
    assert {f.path for f in pack.files} == set(files)

    activity = catalog.get_definition(result.ref, "activity", "diagnose-dns-resolver-failure-v1")
    assert activity.path == ACTIVITY
    assert activity.document == _json(files, ACTIVITY)
    assert activity.document_hash == document_hash(_json(files, ACTIVITY))
    assert not activity.learner_facing
    skill = catalog.get_definition(result.ref, "skill", "network.dns.resolution")
    assert skill.learner_facing and skill.document["mastery_threshold"] == 0.8
    assert len(catalog.list_definitions(result.ref, "activity")) == 2
    layout = catalog.get_definition(result.ref, "layout", "ux/layout.json")
    assert layout.learner_facing

    prompt = catalog.get_prompt(result.ref, "learner-model-update", "1")
    assert prompt.text == files["prompts/learner-model-update.md"].decode()

    with pytest.raises(PackNotFoundError):
        catalog.get_definition(result.ref, "skill", "no.such.skill")


def test_reference_solutions_live_only_in_the_secret_projection() -> None:
    store = InMemoryEventStore()
    ref = _importer(_files(), store).import_pack(LOCATION).ref
    with store.transaction() as tx:
        definitions = tx.list_projection(DEFINITION_PROJECTION)
    assert definitions
    assert all(doc["kind"] != "reference_solution" for _, doc in definitions)
    dumped = json.dumps([doc for _, doc in definitions])
    assert "resolv.conf.good" not in dumped and "$ a search internal" not in dumped

    catalog = PackCatalog(store)
    solution = catalog.get_reference_solution(ref, "diagnose-dns-resolver-failure-v1")
    assert solution.solution_id == "diagnose-dns-resolver-failure-v1.solution"
    assert solution.path == SOLUTION
    with pytest.raises(ValueError):
        catalog.list_definitions(ref, "reference_solution")

    activity = catalog.get_definition(ref, "activity", "diagnose-dns-resolver-failure-v1")
    view = learner_view_of_activity(activity.document)
    assert set(view) <= {
        "title",
        "activity_type",
        "skills",
        "difficulty",
        "instructions",
        "hints",
        "remediation",
    }
    assert "reference_solution" not in view and "checks" not in view


def test_yaml_manifest_is_accepted_and_documents_hash_the_same() -> None:
    files = _files()
    manifest = _json(files, "manifest.json")
    del files["manifest.json"]
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    store = InMemoryEventStore()
    ref = _importer(files, store).import_pack(LOCATION).ref
    pack = PackCatalog(store).get_pack(ref)
    assert pack.manifest == manifest


# --- Refusals ---------------------------------------------------------------


def test_schema_invalid_file_is_rejected() -> None:
    files = _edit(
        _files(),
        "skills/network.dns.resolution.json",
        lambda d: d.update(mastery_threshold=1.5),
    )
    assert ("schema", "skills/network.dns.resolution.json") in _refused(files)


def test_schema_invalid_manifest_is_rejected() -> None:
    files = _edit(_files(), "manifest.json", lambda d: d.pop("registry"))
    assert _refused(files) == [("schema", "manifest.json")]


def test_unregistered_adapter_item_is_rejected() -> None:
    files = _edit(_files(), ACTIVITY, lambda d: d.update(tools=["dns.shell"]))
    assert ("adapter:item_not_registered", ACTIVITY) in _refused(files)


def test_adapter_param_validation_failure_is_rejected() -> None:
    files = _edit(_files(), ACTIVITY, lambda d: d["checks"][0].update(params={"host": "x"}))
    assert ("adapter:invalid_params", ACTIVITY) in _refused(files)


def test_unindexed_and_missing_files_are_rejected() -> None:
    files = _files()
    files["eval/holdout/extra.json"] = b"{}"
    del files["skills/network.dns.resolution.json"]
    problems = _refused(files)
    assert ("file_not_indexed", "eval/holdout/extra.json") in problems
    assert ("indexed_file_missing", "skills/network.dns.resolution.json") in problems


def test_reference_solution_hash_mismatch_is_rejected() -> None:
    files = _edit(_files(), SOLUTION, lambda d: d.update(explanation="Changed."))
    assert ("hash_mismatch", ACTIVITY) in _refused(files)


def test_unresolved_reference_is_rejected() -> None:
    files = _edit(_files(), ACTIVITY, lambda d: d.update(evaluator="no-such-rubric"))
    assert ("unresolved_reference", ACTIVITY) in _refused(files)


def test_non_authoring_timing_is_rejected_in_v01() -> None:
    files = _edit(
        _files(),
        "templates/diagnose-dns-failure.json",
        lambda d: d.update(timing="pooled"),
    )
    assert ("timing_not_supported", "templates/diagnose-dns-failure.json") in _refused(files)


def test_learner_model_selection_missing_parameter_is_rejected() -> None:
    files = _edit(
        _files(), "manifest.json", lambda d: d["registry"]["learner_model"].update(options={})
    )
    assert ("registry", None) in _refused(files)


def test_unknown_learner_model_implementation_is_rejected() -> None:
    files = _edit(
        _files(),
        "manifest.json",
        lambda d: d["registry"]["learner_model"].update(implementation="bkt@1.0.0"),
    )
    assert ("registry", None) in _refused(files)


# --- Content hash -----------------------------------------------------------


def test_content_hash_is_stable_and_changes_on_any_edit() -> None:
    files = _files()
    first = _importer(files).check(LOCATION).ref.content_hash
    assert _importer(dict(reversed(list(files.items())))).check(LOCATION).ref.content_hash == first

    prompt_edit = {**files, "prompts/tutor-reply.md": files["prompts/tutor-reply.md"] + b"\n"}
    manifest_edit = _edit(files, "manifest.json", lambda d: d.update(description="Edited."))
    hashes = {
        first,
        _importer(prompt_edit).check(LOCATION).ref.content_hash,
        _importer(manifest_edit).check(LOCATION).ref.content_hash,
    }
    assert len(hashes) == 3


def test_pack_content_hash_follows_the_defined_algorithm() -> None:
    files = {"b.json": b"{}", "a.md": b"hi"}
    listing = [
        {"path": "a.md", "content_hash": "sha256:" + hashlib.sha256(b"hi").hexdigest()},
        {"path": "b.json", "content_hash": "sha256:" + hashlib.sha256(b"{}").hexdigest()},
    ]
    assert pack_content_hash(files.items()) == document_hash(listing)


# --- No overwrite, idempotency, rebuild -------------------------------------


def test_reimport_is_idempotent_and_never_overwrites() -> None:
    files = _files()
    store = InMemoryEventStore()
    first = _importer(files, store).import_pack(LOCATION)
    again = _importer(files, store).import_pack(LOCATION)
    assert first.created and not again.created and again.ref == first.ref

    # Same pack_version, different content: kept side by side, not overwritten.
    edited = _edit(files, "manifest.json", lambda d: d.update(description="Edited."))
    second = _importer(edited, store).import_pack(LOCATION)
    assert second.created and second.ref.content_hash != first.ref.content_hash
    assert second.ref.pack_version == first.ref.pack_version
    assert sorted(PackCatalog(store).list_packs(), key=lambda r: r.key) == sorted(
        [first.ref, second.ref], key=lambda r: r.key
    )


def test_different_content_under_an_existing_key_is_a_conflict() -> None:
    files = _files()
    store = InMemoryEventStore()
    ref = _importer(files, store).import_pack(LOCATION).ref
    key = ref.definition_key("skill", "network.dns.resolution")
    with store.transaction() as tx:
        doc = dict(tx.get_projection(DEFINITION_PROJECTION, key) or {})
        doc["path"] = "tampered.json"
        tx.put_projection(DEFINITION_PROJECTION, key, doc)
    with pytest.raises(PackProjectionConflictError):
        _importer(files, store).import_pack(LOCATION)
    with store.transaction() as tx:
        assert (tx.get_projection(DEFINITION_PROJECTION, key) or {})["path"] == "tampered.json"


def test_projection_is_rebuilt_by_reimporting() -> None:
    files = _files()
    store = InMemoryEventStore()
    _importer(files, store).import_pack(LOCATION)
    names = (PACK_PROJECTION, DEFINITION_PROJECTION, SECRET_PROJECTION)
    with store.transaction() as tx:
        before = {name: list(tx.list_projection(name)) for name in names}
        for name in names:
            tx.clear_projection(name)
    assert _importer(files, store).import_pack(LOCATION).created
    with store.transaction() as tx:
        after = {name: list(tx.list_projection(name)) for name in names}
    assert canonicalize(json.loads(json.dumps(after))) == canonicalize(
        json.loads(json.dumps(before))
    )


# --- JCS --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (4.5, "4.5"),
        (0.002, "0.002"),
        (0.000001, "0.000001"),
        (1e-7, "1e-7"),
        (1e21, "1e+21"),
        (1e23, "1e+23"),
        (123456789012345680000.0, "123456789012345680000"),
        (333333333.3333333, "333333333.3333333"),
        (-1.5e-10, "-1.5e-10"),
        (2**60, "1152921504606847000"),
    ],
)
def test_jcs_number_formatting(value: float, expected: str) -> None:
    assert canonicalize(value).decode() == expected


def test_jcs_sorts_keys_by_utf16_and_escapes_like_ecmascript() -> None:
    value = {"€": 1, "\r": 2, "דּ": 3, "1": 4, "\U0001f600": 5, "\u0080": 6, "ö": 7}
    keys = json.loads(canonicalize(value).decode())
    assert list(keys) == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
    assert canonicalize('a"\\\n\x01é').decode() == '"a\\"\\\\\\n\\u0001é"'


def test_a_visualization_bound_to_other_lab_values_is_rejected() -> None:
    # The fixture's generated activity serves "api", not "api.internal" (AC-C2, AC-C3).
    viz = "visualizations/dns-resolution-flow.json"
    files = _edit(
        _files(), viz, lambda d: d.update(environment_bindings={"service_name": "api.internal"})
    )
    problems = _refused(files)
    assert ("binding_mismatch", "activities/gen-dns-search-domain-001.json") in problems
    assert ("binding_mismatch", ACTIVITY) not in problems


def test_a_toc_item_must_name_an_existing_definition() -> None:
    items: list[dict[str, str]] = [
        {"kind": "reference", "id": "network.dns.resolver"},
        {"kind": "activity", "id": "diagnose-dns-resolver-failure-v1"},
    ]
    toc = {"chapters": [{"title": "Basics", "items": items}]}
    layout = "ux/layout.json"
    _importer(_edit(_files(), layout, lambda d: d.update(toc=toc))).check(LOCATION)
    items.append({"kind": "visualization", "id": "no-such-viz"})
    assert _refused(_edit(_files(), layout, lambda d: d.update(toc=toc))) == [
        ("unresolved_reference", layout)
    ]
