"""The ``authoring`` content-generation pipeline (ADR-0014; AC-J1, AC-J2, AC-J3, AC-J7).

One path, run from the CLI at authoring time::

    Template (pack data)
      -> Generator (the LLM the pack selects under registry.generator)
      -> candidate (contracts/schemas/llm/generator.activity_candidate)
      -> validation
      -> finalized Definition files written into the pack
      -> generation record written beside them

Validation, in this order, and all of it before anything is written:

``schema``
    the candidate validates against the Generator's output schema, and the
    documents materialized from it validate against the pack schemas for an
    ActivityDefinition, an EnvironmentDefinition and a ReferenceSolution.
``adapter_references``
    the candidate composes only fixtures and checks the Template allows
    (``allowed_fixtures`` / ``allowed_checks``) and that a registered domain
    adapter provides, with params that adapter accepts (ADR-0009).
``checks_fail_in_broken_state`` / ``checks_pass_after_solution``
    for a lab-backed activity, a real lab is started from the generated
    environment: the activity must not already succeed in the broken state,
    and every check must pass after the reference solution is applied (AC-J2).
    Each phase gets its own freshly started lab, because a reference solution
    is defined to run on a fresh lab and a check is free to change the lab.

A candidate that fails any step is never written; it is summarized in the
record's ``rejected_candidates`` and a new candidate is requested, up to the
pack's ``content_generation.max_regenerations`` retries. Before the first byte
is written, the whole would-be pack (the existing files plus the generated
ones plus the updated manifest index) is run through the
:class:`~harness.core.pack.PackImporter` over an
:class:`~harness.cli.pack_files.OverlayPackSource`, so a pack that would not
import is never created.

A finalized Definition is immutable (ADR-0014): this command only ever writes
new files. If the activity id or any of its paths already exists in the pack,
the run is refused; the automatic id picks the next free suffix instead.

The reference solution is written to its own file, bound to the activity by
path and hash, and never merged into the activity document (AC-J6).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from harness.cli.errors import CommandError
from harness.cli.pack_files import OverlayPackSource, PackWriter, dump_document
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    DomainAdapterRegistry,
    ItemKind,
)
from harness.core.generator.validate import (
    AUTHORING,
    CANDIDATE_SCHEMA_ID,
    SCHEMA_STEP,
    Accepted,
    Candidate,
    CandidateValidator,
    Paths,
    Rejected,
    default_lab_id,
)
from harness.core.pack import PackImporter, PackImportError, document_hash
from harness.core.pack.importer import ParsedFile, ParsedPack
from harness.core.ports import (
    LabRuntime,
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvider,
    LLMRequest,
    PackSource,
    PlainJson,
    format_timestamp,
    to_plain_object,
)
from harness.core.registry import AlgorithmError, RegistrySelection

RECORD_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/generation-record.json")

GENERATOR_ROLE = "generator"
ACTIVITY_DIR = "activities"
ENVIRONMENT_DIR = "environments"
VISUALIZATION_DIR = "visualizations"
ID_PREFIX = "gen-"
MAX_AUTO_SUFFIX = 999

EXISTING_ACTIVITY_SUMMARIES = 20
"""How many already generated activities of this Template are described to the Generator."""

MISSION_SUMMARY_CHARS = 400

type Document = dict[str, PlainJson]


# --- Values -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateRequest:
    """One ``generate`` invocation.

    ``activity_id`` ``None`` picks the next free ``gen-<template id>-<NNN>``;
    ``max_attempts`` ``None`` uses the pack's ``max_regenerations`` plus one.
    """

    location: str
    template_id: str
    activity_id: str | None = None
    max_attempts: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectedCandidate:
    """A candidate that failed validation, as the record summarizes it.

    The candidate's own content is deliberately not kept: it may hold a
    reference solution, and a rejected candidate is never served anyway.
    """

    attempt: int
    failed_step: str
    reason: str

    def to_dict(self) -> Document:
        return {"attempt": self.attempt, "failed_step": self.failed_step, "reason": self.reason}


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateResult:
    activity_id: str
    attempts: int
    written: tuple[str, ...]
    rejected: tuple[RejectedCandidate, ...]


# --- JSON helpers -----------------------------------------------------------


def _obj(value: PlainJson | None) -> Document:
    assert isinstance(value, dict)
    return value


def _items(value: PlainJson | None) -> list[PlainJson]:
    return value if isinstance(value, list) else []


def _text(value: PlainJson | None) -> str:
    assert isinstance(value, str)
    return value


# --- Generator --------------------------------------------------------------


class ActivityGenerator:
    """Runs the pipeline described in the module docstring.

    Everything it touches is injected, so tests drive the same code with a
    scripted LLM, an in-memory pack and a fake lab runtime. ``importer_for``
    builds a :class:`~harness.core.pack.PackImporter` over a given pack source
    (the pack as it is first, the overlay with the generated files at the end).
    """

    def __init__(
        self,
        *,
        source: PackSource,
        writer: PackWriter,
        schemas: ContractSchemas,
        adapters: DomainAdapterRegistry,
        llm: LLMProvider,
        lab: LabRuntime,
        importer_for: Callable[[PackSource], PackImporter],
        now: Callable[[], datetime],
        new_lab_id: Callable[[], str] = default_lab_id,
    ) -> None:
        self._source = source
        self._writer = writer
        self._schemas = schemas
        self._adapters = adapters
        self._llm = llm
        self._importer_for = importer_for
        self._now = now
        self._validator = CandidateValidator(
            schemas=schemas, adapters=adapters, lab=lab, new_lab_id=new_lab_id
        )

    # --- public API ---------------------------------------------------------

    def run(self, request: GenerateRequest) -> GenerateResult:
        location = request.location
        parsed = self._check(self._source, location, "the pack does not import")
        by_kind = _by_kind(parsed)
        on_disk = set(self._source.list_files(location))

        template_file = by_kind.get("activity_template", {}).get(request.template_id)
        if template_file is None:
            known = sorted(by_kind.get("activity_template", {}))
            raise CommandError(
                f"no activity_template {request.template_id!r} in {location}; "
                f"the pack declares {known}"
            )
        template = _obj(template_file.content)
        self._check_timing(parsed.manifest, template)

        selection = self._selection(parsed.manifest)
        prompt = self._prompt(parsed, selection)
        activity_id = _activity_id(request, by_kind, on_disk)
        paths = _paths(activity_id)
        _refuse_existing(location, paths, activity_id, by_kind, on_disk)

        attempts = request.max_attempts or _max_regenerations(parsed.manifest) + 1
        if attempts < 1:
            raise CommandError("the number of attempts must be at least 1")

        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=self._context(template, by_kind)),
        ]
        rejected: list[RejectedCandidate] = []
        accepted: Accepted | None = None
        attempt = 0
        for attempt in range(1, attempts + 1):
            candidate = self._ask(selection, messages)
            try:
                if candidate.output is None:
                    raise Rejected(SCHEMA_STEP, "the provider returned no JSON object")
                accepted = self._validator.validate(
                    candidate=candidate,
                    manifest=parsed.manifest,
                    template=template,
                    activity_id=activity_id,
                    paths=paths,
                    visualizations={
                        key: _obj(f.content) for key, f in by_kind.get("visualization", {}).items()
                    },
                )
            except Rejected as exc:
                rejected.append(
                    RejectedCandidate(attempt=attempt, failed_step=exc.step, reason=exc.reason)
                )
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            f"The previous candidate was rejected at validation step "
                            f"{exc.step!r}: {exc.reason}\nReturn a corrected candidate."
                        ),
                    )
                )
                continue
            break

        if accepted is None:
            raise CommandError(
                f"no candidate passed validation in {attempts} attempt(s):\n"
                + "\n".join(f"  attempt {r.attempt} [{r.failed_step}] {r.reason}" for r in rejected)
            )

        record = self._record(
            template_file=template_file,
            selection=selection,
            accepted=accepted,
            paths=paths,
            rejected=rejected,
        )
        pending = _pending(parsed, accepted, record, paths)
        self._check(
            OverlayPackSource(self._source, location=location, files=pending),
            location,
            "the generated pack would not import",
        )
        for path, data in pending.items():
            self._writer.write_bytes(location, path, data, replace=path == parsed.manifest_path)
        return GenerateResult(
            activity_id=activity_id,
            attempts=attempt,
            written=tuple(p for p in pending if p != parsed.manifest_path),
            rejected=tuple(rejected),
        )

    # --- pack ---------------------------------------------------------------

    def _check(self, source: PackSource, location: str, message: str) -> ParsedPack:
        try:
            return self._importer_for(source).check(location)
        except PackImportError as exc:
            raise CommandError(f"{message}:\n" + "\n".join(f"  {p}" for p in exc.problems)) from exc

    def _check_timing(self, manifest: Mapping[str, PlainJson], template: Document) -> None:
        timing = template.get("timing", _default_timing(manifest))
        if timing != AUTHORING:
            raise CommandError(
                f"template {template['id']!r} has generation timing {timing!r}; this command "
                f"runs the {AUTHORING!r} timing only (v0.1, ADR-0014)"
            )

    def _selection(self, manifest: Mapping[str, PlainJson]) -> RegistrySelection:
        registry = _obj(manifest["registry"])
        if GENERATOR_ROLE not in registry:
            raise CommandError("the pack manifest declares no registry.generator")
        try:
            selection = RegistrySelection.parse(GENERATOR_ROLE, registry[GENERATOR_ROLE])
        except AlgorithmError as exc:
            raise CommandError(str(exc)) from exc
        if selection.output_schema != CANDIDATE_SCHEMA_ID:
            raise CommandError(
                f"registry.generator.output_schema is {selection.output_schema!r}; this "
                f"pipeline materializes {CANDIDATE_SCHEMA_ID!r}"
            )
        return selection

    def _prompt(self, parsed: ParsedPack, selection: RegistrySelection) -> str:
        wanted = (selection.llm.prompt_id, selection.llm.prompt_version)
        for file in parsed.files:
            if file.prompt == wanted:
                return _text(file.content)
        raise CommandError(f"the pack has no prompt {wanted[0]!r} version {wanted[1]!r}")

    # --- the Generator call -------------------------------------------------

    def _context(self, template: Document, by_kind: Mapping[str, Mapping[str, ParsedFile]]) -> str:
        """The user message: the Template, the adapter items it allows, what exists."""
        documentation: Document = {}
        items: list[PlainJson] = []
        allowed: tuple[tuple[ItemKind, str], ...] = (
            ("fixture", "allowed_fixtures"),
            ("check", "allowed_checks"),
        )
        for kind, field in allowed:
            for item_id in _items(template.get(field)):
                items.append(self._describe(kind, _text(item_id), documentation))
        context: Document = {
            "template": {
                name: template[name]
                for name in (
                    "id",
                    "activity_type",
                    "skills",
                    "difficulty",
                    "allowed_fixtures",
                    "allowed_checks",
                    "tools",
                    "brief",
                )
                if name in template
            },
            "adapter_items": items,
            "adapter_documentation": documentation,
            "existing_activities": _existing_summaries(template, by_kind),
        }
        return json.dumps(context, indent=2, ensure_ascii=False)

    def _describe(self, kind: ItemKind, item_id: str, documentation: Document) -> Document:
        """One allowed adapter item, described from the registered implementation.

        A domain adapter documents its items and their parameter spec in its
        own docstrings and has no separate machine-readable spec, so those
        docstrings are what the Generator is given.
        """
        item = (
            self._adapters.fixture(item_id) if kind == "fixture" else self._adapters.check(item_id)
        )
        implementation = type(item)
        source = implementation.__module__
        if source not in documentation:
            module = sys.modules.get(source)
            documentation[source] = (getattr(module, "__doc__", None) or "").strip()
        return {
            "item_id": item_id,
            "kind": kind,
            "description": (implementation.__doc__ or "").strip(),
            "documentation": source,
        }

    def _ask(self, selection: RegistrySelection, messages: Sequence[LLMMessage]) -> Candidate:
        request = LLMRequest(
            role="generator",
            llm=selection.llm,
            messages=list(messages),
            output_schema_id=CANDIDATE_SCHEMA_ID,
            output_schema=self._schemas.bundle(CANDIDATE_SCHEMA_ID),
        )
        try:
            response = self._llm.complete_structured(request)
        except LLMOutputError:
            return Candidate(output=None, provenance=selection.llm)
        except LLMError as exc:  # transport, auth, rate limit: not the candidate's fault
            raise CommandError(f"the Generator call failed: {exc}") from exc
        return Candidate(output=to_plain_object(response.output), provenance=response.provenance)

    # --- validation ---------------------------------------------------------

    # --- the record ---------------------------------------------------------

    def _record(
        self,
        *,
        template_file: ParsedFile,
        selection: RegistrySelection,
        accepted: Accepted,
        paths: Paths,
        rejected: Sequence[RejectedCandidate],
    ) -> Document:
        outputs: list[PlainJson] = [
            _output("activity", paths.activity, accepted.activity),
        ]
        if accepted.environment is not None:
            outputs.append(_output("environment", paths.environment, accepted.environment))
        outputs.append(_output("reference_solution", paths.solution, accepted.solution))
        outputs += [
            _output("visualization", _visualization_path(v), v) for v in accepted.visualizations
        ]
        record: Document = {
            "timing": AUTHORING,
            "template": {
                "template_id": _obj(template_file.content)["id"],
                "template_hash": template_file.document_hash,
            },
            "generator": {
                "implementation": selection.implementation,
                "llm": accepted.provenance.to_dict(),
            },
            "generated_at": format_timestamp(self._now()),
            "outputs": outputs,
            "validation": {"outcome": "passed", "steps": [dict(s) for s in accepted.steps]},
            "rejected_candidates": [r.to_dict() for r in rejected],
        }
        self._schemas.validate(record, RECORD_SCHEMA_ID)
        return record


# --- module helpers ---------------------------------------------------------


def _by_kind(parsed: ParsedPack) -> dict[str, dict[str, ParsedFile]]:
    out: dict[str, dict[str, ParsedFile]] = {}
    for file in parsed.files:
        if file.definition_key is not None:
            out.setdefault(file.kind, {})[file.definition_key] = file
    return out


def _paths(activity_id: str) -> Paths:
    return Paths(
        activity=f"{ACTIVITY_DIR}/{activity_id}.json",
        environment=f"{ENVIRONMENT_DIR}/{activity_id}.json",
        solution=f"{ACTIVITY_DIR}/{activity_id}.solution.json",
        record=f"{ACTIVITY_DIR}/{activity_id}.generation.json",
    )


def _activity_id(
    request: GenerateRequest,
    by_kind: Mapping[str, Mapping[str, ParsedFile]],
    on_disk: set[str],
) -> str:
    if request.activity_id is not None:
        return request.activity_id
    taken = set(by_kind.get("activity", {}))
    base = f"{ID_PREFIX}{request.template_id}"
    for suffix in range(1, MAX_AUTO_SUFFIX + 1):
        candidate = f"{base}-{suffix:03d}"
        if candidate in taken or on_disk & set(_paths(candidate).every()):
            continue
        return candidate
    raise CommandError(f"no free activity id under {base!r}")


def _refuse_existing(
    location: str,
    paths: Paths,
    activity_id: str,
    by_kind: Mapping[str, Mapping[str, ParsedFile]],
    on_disk: set[str],
) -> None:
    """A finalized Definition is immutable: never write over one (ADR-0014)."""
    bound_prefix = f"{VISUALIZATION_DIR}/{activity_id}."
    clash = sorted(
        on_disk & set(paths.every()) | {p for p in on_disk if p.startswith(bound_prefix)}
    )
    if clash:
        raise CommandError(
            f"{location} already holds {clash}; a finalized Definition is never rewritten "
            "(ADR-0014) -- use a new activity id"
        )
    for kind, definition_id in (
        ("activity", activity_id),
        ("environment", f"{activity_id}.env"),
        ("reference_solution", f"{activity_id}.solution"),
    ):
        if definition_id in by_kind.get(kind, {}):
            raise CommandError(
                f"the pack already defines {kind} {definition_id!r}; a finalized Definition "
                "is never rewritten (ADR-0014)"
            )


def _existing_summaries(
    template: Document, by_kind: Mapping[str, Mapping[str, ParsedFile]]
) -> list[PlainJson]:
    summaries: list[PlainJson] = []
    for file in by_kind.get("activity", {}).values():
        document = _obj(file.content)
        origin = _obj(document["origin"])
        if origin.get("template_id") != template["id"]:
            continue
        summaries.append(
            {
                "id": document["id"],
                "title": document["title"],
                "mission": _text(_obj(document["instructions"])["mission"])[:MISSION_SUMMARY_CHARS],
            }
        )
    return summaries[-EXISTING_ACTIVITY_SUMMARIES:]


def _default_timing(manifest: Mapping[str, PlainJson]) -> PlainJson:
    generation = manifest.get("content_generation")
    return _obj(generation)["default_timing"] if isinstance(generation, dict) else None


def _max_regenerations(manifest: Mapping[str, PlainJson]) -> int:
    generation = manifest.get("content_generation")
    value = _obj(generation)["max_regenerations"] if isinstance(generation, dict) else None
    assert isinstance(value, int)
    return value


def _visualization_path(visualization: Document) -> str:
    return f"{VISUALIZATION_DIR}/{_text(visualization['id'])}.json"


def _output(kind: str, path: str, document: Document) -> PlainJson:
    return {
        "kind": kind,
        "path": path,
        "definition_id": document["id"],
        "definition_hash": document_hash(document),
    }


def _pending(
    parsed: ParsedPack, accepted: Accepted, record: Document, paths: Paths
) -> dict[str, bytes]:
    """Every file this run would write, including the updated manifest file index."""
    files: dict[str, bytes] = {
        paths.activity: dump_document(accepted.activity),
        paths.solution: dump_document(accepted.solution),
        paths.record: dump_document(record),
    }
    index: dict[str, PlainJson] = {
        paths.activity: {"kind": "activity"},
        paths.solution: {"kind": "reference_solution"},
        paths.record: {"kind": "generation_record"},
    }
    if accepted.environment is not None:
        files[paths.environment] = dump_document(accepted.environment)
        index[paths.environment] = {"kind": "environment"}
    for visualization in accepted.visualizations:
        files[_visualization_path(visualization)] = dump_document(visualization)
        index[_visualization_path(visualization)] = {"kind": "visualization"}
    manifest = dict(parsed.manifest)
    merged = {**_obj(manifest["files"]), **index}
    manifest["files"] = {path: merged[path] for path in sorted(merged)}
    files[parsed.manifest_path] = dump_document(manifest)
    return files
