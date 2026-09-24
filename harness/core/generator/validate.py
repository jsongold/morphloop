"""Validation of one Generator candidate (ADR-0014; AC-J2).

Adapter-independent: it composes Definitions from a candidate, validates them
against the contract schemas and the registered domain adapters, and, for a
lab-backed activity, runs the checks through the :class:`LabRuntime` Port. See
:mod:`harness.cli.generate` for the pipeline around it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    AdapterParamsError,
    DomainAdapterRegistry,
    ItemReference,
    run_check,
)
from harness.core.pack import document_hash
from harness.core.ports import (
    ExecRequest,
    ImageRef,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    LLMProvenance,
    PlainJson,
)

CANDIDATE_SCHEMA_ID = ContractSchemas.id_for("schemas/llm/generator.activity_candidate/1.json")
ACTIVITY_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/activity-definition.json")
ENVIRONMENT_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/environment.json")
SOLUTION_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/reference-solution.json")
VISUALIZATION_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/visualization.json")

AUTHORING = "authoring"

SCHEMA_STEP = "schema"
ADAPTER_STEP = "adapter_references"
BROKEN_STEP = "checks_fail_in_broken_state"
FIXED_STEP = "checks_pass_after_solution"

SOLUTION_MAX_OUTPUT_BYTES = 64 * 1024
"""Per-stream output kept from a reference-solution step; only used to report a failure."""

type Document = dict[str, PlainJson]


class Rejected(Exception):
    """Internal: this candidate failed ``step``; ask for another one."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


@dataclass(frozen=True, slots=True, kw_only=True)
class Paths:
    activity: str
    environment: str
    solution: str
    record: str

    def every(self) -> tuple[str, ...]:
        return (self.activity, self.environment, self.solution, self.record)


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    """One Generator answer. ``output`` is ``None`` when nothing usable came back."""

    output: Document | None
    provenance: LLMProvenance


@dataclass(frozen=True, slots=True, kw_only=True)
class Accepted:
    """The documents and the validation steps of the candidate that passed."""

    activity: Document
    environment: Document | None
    solution: Document
    visualizations: tuple[Document, ...]
    steps: tuple[Document, ...]
    provenance: LLMProvenance


# --- JSON helpers -----------------------------------------------------------


def _obj(value: PlainJson | None) -> Document:
    assert isinstance(value, dict)
    return value


def _items(value: PlainJson | None) -> list[PlainJson]:
    return value if isinstance(value, list) else []


def _text(value: PlainJson | None) -> str:
    assert isinstance(value, str)
    return value


def default_lab_id() -> str:
    """``lab_<32 hex>`` (``contracts/schemas/common/ids.json``)."""
    return f"lab_{uuid4().hex}"


# --- Validator --------------------------------------------------------------


class CandidateValidator:
    """Validates one candidate; every dependency is a core type or a Port."""

    def __init__(
        self,
        *,
        schemas: ContractSchemas,
        adapters: DomainAdapterRegistry,
        lab: LabRuntime,
        new_lab_id: Callable[[], str] = default_lab_id,
    ) -> None:
        self._schemas = schemas
        self._adapters = adapters
        self._lab = lab
        self._new_lab_id = new_lab_id

    def validate(
        self,
        *,
        candidate: Candidate,
        manifest: Mapping[str, PlainJson],
        template: Document,
        activity_id: str,
        paths: Paths,
        visualizations: Mapping[str, Document],
    ) -> Accepted:
        output = candidate.output
        assert output is not None
        errors = self._schemas.errors(output, CANDIDATE_SCHEMA_ID)
        if errors:
            raise Rejected(SCHEMA_STEP, "; ".join(errors))

        environment, references = self._compose_environment(output, template, activity_id, paths)
        checks, check_references = _compose_checks(output, template, paths)
        references += check_references
        references += [
            ItemReference(
                kind="tool",
                item_id=_text(tool),
                params=None,
                source=f"{paths.activity}#/tools/{index}",
            )
            for index, tool in enumerate(_items(template.get("tools")))
        ]

        solution = _solution_document(
            output, template, activity_id, has_lab=environment is not None
        )
        activity = _activity_document(
            candidate=output,
            template=template,
            activity_id=activity_id,
            checks=checks,
            environment_id=None if environment is None else _text(environment["id"]),
            solution_path=paths.solution,
            solution_hash=document_hash(solution),
        )
        bound = _bind_visualizations(activity, environment, visualizations)
        documents: list[tuple[Document, str, str]] = [
            (activity, ACTIVITY_SCHEMA_ID, "activity"),
            (solution, SOLUTION_SCHEMA_ID, "reference solution"),
        ]
        documents += [(v, VISUALIZATION_SCHEMA_ID, f"visualization {v['id']}") for v in bound]
        if environment is not None:
            documents.append((environment, ENVIRONMENT_SCHEMA_ID, "environment"))
        for document, schema_id, where in documents:
            errors = self._schemas.errors(document, schema_id)
            if errors:
                raise Rejected(SCHEMA_STEP, f"{where}: " + "; ".join(errors))

        requirements = {
            adapter_id: version
            for adapter_id, version in _obj(manifest["domain_adapters"]).items()
            if isinstance(version, str)
        }
        problems = self._adapters.verify(requirements, references)
        if problems:
            raise Rejected(
                ADAPTER_STEP, "; ".join(f"{p.source or p.subject}: {p.message}" for p in problems)
            )

        steps: list[Document] = [
            {"step": SCHEMA_STEP, "outcome": "passed", "lab": None},
            {"step": ADAPTER_STEP, "outcome": "passed", "lab": None},
        ]
        if environment is not None:
            steps += self._validate_in_lab(
                environment=environment, checks=checks, solution=solution
            )
        return Accepted(
            activity=activity,
            environment=environment,
            solution=solution,
            visualizations=tuple(bound),
            steps=tuple(steps),
            provenance=candidate.provenance,
        )

    def _compose_environment(
        self, output: Document, template: Document, activity_id: str, paths: Paths
    ) -> tuple[Document | None, list[ItemReference]]:
        fixtures = _items(output.get("fixtures"))
        image = template.get("image")
        if not fixtures:
            return None, []
        if len(fixtures) > 1:
            raise Rejected(
                ADAPTER_STEP,
                "one EnvironmentDefinition names exactly one fixture; the candidate "
                f"composes {len(fixtures)}",
            )
        if image is None:
            raise Rejected(
                ADAPTER_STEP, "the Template pins no lab image, so the activity can use no fixture"
            )
        fixture = _obj(fixtures[0])
        fixture_id = _text(fixture["fixture_id"])
        if fixture_id not in [_text(f) for f in _items(template.get("allowed_fixtures"))]:
            raise Rejected(
                ADAPTER_STEP, f"fixture {fixture_id!r} is not in the Template's allowed_fixtures"
            )
        params = _params(fixture.get("params"), "fixtures[0].params")
        environment: Document = {
            "id": f"{activity_id}.env",
            "fixture": fixture_id,
            "image": image,
            "params": params,
        }
        reference = ItemReference(
            kind="fixture",
            item_id=fixture_id,
            params=params,
            source=f"{paths.environment}#/fixture",
        )
        return environment, [reference]

    def _validate_in_lab(
        self,
        *,
        environment: Document,
        checks: Sequence[PlainJson],
        solution: Document,
    ) -> list[Document]:
        """AC-J2: the checks must fail broken and pass after the reference solution."""
        fixture_id = _text(environment["fixture"])
        image_document = _obj(environment["image"])
        image = ImageRef(
            repository=_text(image_document["repository"]),
            digest=_text(image_document["digest"]),
        )
        try:
            spec = self._adapters.fixture(fixture_id).build_lab_spec(
                image, _obj(environment["params"])
            )
        except AdapterParamsError as exc:
            raise Rejected(BROKEN_STEP, f"the fixture refused the params: {exc}") from exc

        broken = self._start(spec, BROKEN_STEP)
        try:
            if all(passed for _, passed in self._run_checks(broken, checks, BROKEN_STEP)):
                raise Rejected(
                    BROKEN_STEP,
                    "every check already passes in the generated state, so the activity is "
                    "solved before the learner starts",
                )
        finally:
            self._lab.destroy(broken)

        fixed = self._start(spec, FIXED_STEP)
        try:
            self._apply(fixed, solution)
            failed = [
                check_id
                for check_id, passed in self._run_checks(fixed, checks, FIXED_STEP)
                if not passed
            ]
            if failed:
                raise Rejected(
                    FIXED_STEP, f"after the reference solution these checks still fail: {failed}"
                )
        finally:
            self._lab.destroy(fixed)

        provenance: Document = {"image_digest": image.digest, "fixture_id": fixture_id}
        return [
            {"step": BROKEN_STEP, "outcome": "passed", "lab": dict(provenance)},
            {"step": FIXED_STEP, "outcome": "passed", "lab": dict(provenance)},
        ]

    def _start(self, spec: LabSpec, step: str) -> str:
        lab_instance_id = self._new_lab_id()
        try:
            self._lab.start(lab_instance_id, spec)
        except LabRuntimeError as exc:
            raise Rejected(step, f"the generated lab did not come up: {exc}") from exc
        return lab_instance_id

    def _run_checks(
        self, lab_instance_id: str, checks: Sequence[PlainJson], step: str
    ) -> list[tuple[str, bool]]:
        results: list[tuple[str, bool]] = []
        for entry in checks:
            check = _obj(entry)
            check_id = _text(check["check"])
            try:
                result = run_check(
                    self._adapters,
                    check_id,
                    _obj(check["params"]),
                    lab=self._lab,
                    lab_instance_id=lab_instance_id,
                )
            except LabRuntimeError as exc:
                raise Rejected(step, f"check {check_id!r} could not run: {exc}") from exc
            results.append((check_id, result.passed))
        return results

    def _apply(self, lab_instance_id: str, solution: Document) -> None:
        for index, entry in enumerate(_items(solution["apply"])):
            step = _obj(entry)
            argv = tuple(_text(a) for a in _items(step["argv"]))
            timeout = step["timeout_seconds"]
            assert isinstance(timeout, int)
            try:
                result = self._lab.exec(
                    lab_instance_id,
                    ExecRequest(
                        argv=argv,
                        timeout_seconds=float(timeout),
                        max_output_bytes=SOLUTION_MAX_OUTPUT_BYTES,
                        env={},
                        workdir=None,
                    ),
                )
            except LabRuntimeError as exc:
                raise Rejected(FIXED_STEP, f"apply[{index}] could not run: {exc}") from exc
            if result.timed_out or result.exit_code != 0:
                tail = result.stderr.decode("utf-8", errors="replace")[-500:]
                raise Rejected(
                    FIXED_STEP,
                    f"apply[{index}] {list(argv)} exited {result.exit_code!r}"
                    f"{' (timed out)' if result.timed_out else ''}: {tail}",
                )


# --- module helpers ---------------------------------------------------------


def _params(entries: PlainJson | None, where: str) -> Document:
    """``[{"name", "value"}]`` -> an adapter params object; names must be unique."""
    params: Document = {}
    for index, entry in enumerate(_items(entries)):
        parameter = _obj(entry)
        name = _text(parameter["name"])
        if name in params:
            raise Rejected(ADAPTER_STEP, f"{where}[{index}]: duplicate parameter {name!r}")
        params[name] = parameter["value"]
    return params


def _compose_checks(
    output: Document, template: Document, paths: Paths
) -> tuple[list[PlainJson], list[ItemReference]]:
    allowed = [_text(c) for c in _items(template.get("allowed_checks"))]
    checks: list[PlainJson] = []
    references: list[ItemReference] = []
    for index, entry in enumerate(_items(output.get("checks"))):
        check = _obj(entry)
        check_id = _text(check["check_id"])
        if check_id not in allowed:
            raise Rejected(
                ADAPTER_STEP, f"check {check_id!r} is not in the Template's allowed_checks"
            )
        params = _params(check.get("params"), f"checks[{index}].params")
        checks.append({"check": check_id, "params": params})
        references.append(
            ItemReference(
                kind="check",
                item_id=check_id,
                params=params,
                source=f"{paths.activity}#/checks/{index}",
            )
        )
    return checks, references


def _solution_document(
    output: Document, template: Document, activity_id: str, *, has_lab: bool
) -> Document:
    source = _obj(output["reference_solution"])
    steps = _items(source.get("sandbox_steps"))
    if steps and not has_lab:
        raise Rejected(
            ADAPTER_STEP, "the activity has no lab, so its reference solution runs no commands"
        )
    timeout = template["solution_step_timeout_seconds"]
    return {
        "id": f"{activity_id}.solution",
        "activity_id": activity_id,
        "apply": [{"argv": _obj(step)["argv"], "timeout_seconds": timeout} for step in steps],
        "explanation": source["explanation"],
    }


def _activity_document(
    *,
    candidate: Document,
    template: Document,
    activity_id: str,
    checks: Sequence[PlainJson],
    environment_id: str | None,
    solution_path: str,
    solution_hash: str,
) -> Document:
    """Merge what the Template declares with what the Generator decided (ADR-0014)."""
    document: Document = {
        "id": activity_id,
        "title": candidate["title"],
        "activity_type": template["activity_type"],
        "skills": template["skills"],
    }
    if "difficulty" in template:
        document["difficulty"] = template["difficulty"]
    document["origin"] = {
        "type": "generated",
        "template_id": template["id"],
        "timing": AUTHORING,
    }
    document["instructions"] = {"mission": candidate["mission"]}
    hints = _items(candidate.get("hints"))
    if hints:
        document["hints"] = list(hints)
    if environment_id is not None:
        document["environment"] = environment_id
    document["tools"] = list(_items(template.get("tools")))
    document["checks"] = list(checks)
    document["evaluator"] = template["evaluator"]
    document["reference_solution"] = {"path": solution_path, "content_hash": solution_hash}
    if "remediation" in template:
        document["remediation"] = template["remediation"]
    return document


def _bind_visualizations(
    activity: Document, environment: Document | None, sources: Mapping[str, Document]
) -> list[Document]:
    """Per-activity copies of the bound visualizations the activity names (AC-C2, AC-C3).

    A visualization with ``environment_bindings`` shows literal lab values (a
    host, a port...). Its copy swaps each literal for this environment's value,
    so every command in it targets the generated lab; the activity then names
    the copy. Unbound visualizations are shared as they are.
    """
    remediation = activity.get("remediation")
    if not isinstance(remediation, dict):
        return []
    params = _obj(environment["params"]) if environment is not None else {}
    bound: list[Document] = []
    names: list[PlainJson] = []
    for entry in _items(remediation.get("visualizations")):
        source = sources.get(_text(entry))
        bindings = source.get("environment_bindings") if source is not None else None
        if source is None or not isinstance(bindings, dict):
            names.append(entry)
            continue
        missing = sorted(set(bindings) - set(params))
        if missing:
            raise Rejected(
                ADAPTER_STEP,
                f"visualization {entry!r} shows environment params {missing}, "
                "which the environment does not set",
            )
        swap = {str(old): str(params[name]) for name, old in bindings.items()}
        copy = _obj(_replace_literals(source, swap))
        copy["id"] = f"{activity['id']}.{entry}"
        copy["environment_bindings"] = {name: params[name] for name in bindings}
        bound.append(copy)
        names.append(copy["id"])
    activity["remediation"] = {**remediation, "visualizations": names}
    return bound


def _replace_literals(value: PlainJson, swap: Mapping[str, str]) -> PlainJson:
    """Every string in ``value`` with each ``swap`` key replaced at once, longest first."""
    # ponytail: plain literal replacement; a bound literal must not occur as unrelated text.
    pattern = re.compile("|".join(re.escape(old) for old in sorted(swap, key=len, reverse=True)))
    if isinstance(value, str):
        return pattern.sub(lambda m: swap[m.group()], value)
    if isinstance(value, list):
        return [_replace_literals(v, swap) for v in value]
    if isinstance(value, dict):
        return {k: _replace_literals(v, swap) for k, v in value.items()}
    return value
