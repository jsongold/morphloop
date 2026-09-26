"""Domain adapter contract and registry (ADR-0009, ADR-0012, ADR-0014).

A domain adapter (shipped by an app) holds domain-specific code: deterministic
checks, environment fixture providers and tool adapters. A pack refers to them
only by adapter item id, ``'<adapter_id>.<name>'`` (``contracts/schemas/common/
ids.json#/$defs/adapter_item_id``), and its manifest declares every adapter it
needs with a version range (``pack/manifest.json#/properties/domain_adapters``).
This module is the interface a domain adapter implements and the registry core
uses to resolve and verify those references.

Why here and not under ``harness.core.ports``: a Port is the seam to a swappable
technical library (ADR-0015, ADR-0016 fix five of them), implemented by
``harness/adapters/*``. A domain adapter is a different layer (ADR-0015 section
2, ADR-0017): it is not swapped per deployment, several are registered at once,
and core also owns the registry around it. Keeping it out of ``ports/`` keeps
that package exactly the five Ports. Like the Ports, this module is imported by
domain adapters and never imports them (``.importlinter``).

Scope is the v0.1 DNS slice (ADR-0012): the three item kinds ADR-0009 names, and
a terminal as the only tool kind. Conventions follow ``harness.core.ports``:
structural, non-``runtime_checkable`` Protocols; frozen ``slots``/``kw_only``
dataclasses; synchronous calls; no defaults for tunable values (ADR-0002).

Execution boundary (ADR-0009). Adapter code runs on the host as reviewed harness
code. Anything derived from a pack (params, argv) may only reach the lab through
:class:`~harness.core.ports.LabRuntime` (``exec`` inside the sandbox) or a
:class:`~harness.core.ports.LabSpec`; an adapter must never hand it to a host
shell or process.

Version ranges. An adapter's ``version`` must be SemVer 2.0.0
(``MAJOR.MINOR.PATCH[-pre][+build]``). A manifest range is a comma-separated list
of comparators (``>=``, ``<=``, ``>``, ``<``, ``==``), all of which must hold
(``pack/defs.json#/$defs/version_range``). Comparison is SemVer precedence: build
metadata is ignored and a pre-release sorts before its release. There is no
npm-style rule that hides pre-releases from a range.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_object
from harness.core.ports.lab_runtime import Argv, ImageRef, LabRuntime, LabSpec, check_argv

type ItemKind = Literal["fixture", "check", "tool"]
type ProblemCode = Literal[
    "adapter_not_registered",
    "invalid_version_range",
    "version_out_of_range",
    "adapter_not_declared",
    "item_not_registered",
    "invalid_params",
]

# contracts/schemas/common/ids.json#/$defs/adapter_id and adapter_item_id.
_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|==)(.+)$")


# --- Errors -----------------------------------------------------------------


class DomainAdapterError(Exception):
    """Base class for domain adapter and registry failures."""


class AdapterParamsError(DomainAdapterError, ValueError):
    """Raised by ``validate_params`` when pack-declared params are invalid."""


class UnregisteredItemError(DomainAdapterError, LookupError):
    """An adapter item id does not resolve to a registered item of that kind."""

    def __init__(self, kind: ItemKind, item_id: str) -> None:
        super().__init__(f"{kind} {item_id!r} is not registered")
        self.kind = kind
        self.item_id = item_id


# --- SemVer -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemVer:
    """A parsed SemVer 2.0.0 version, ordered by SemVer precedence."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...]

    @classmethod
    def parse(cls, text: str) -> SemVer:
        match = _SEMVER_RE.fullmatch(text)
        if match is None:
            raise ValueError(f"not a SemVer 2.0.0 version: {text!r}")
        pre = match.group(4)
        ids: tuple[int | str, ...] = ()
        if pre:
            ids = tuple(int(p) if p.isdigit() else p for p in pre.split("."))
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), ids)

    def _key(self) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
        # A release (no pre-release) sorts after every pre-release of it.
        # Numeric identifiers sort before alphanumeric ones.
        pre = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, pre)

    def __lt__(self, other: SemVer) -> bool:
        return self._key() < other._key()

    def __le__(self, other: SemVer) -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: SemVer) -> bool:
        return self._key() > other._key()

    def __ge__(self, other: SemVer) -> bool:
        return self._key() >= other._key()


def version_satisfies(version: str, version_range: str) -> bool:
    """Return whether SemVer ``version`` satisfies every comparator of ``version_range``.

    Raises ``ValueError`` if either is malformed (see the module docstring).
    """
    current = SemVer.parse(version)
    result = True
    for comparator in version_range.split(","):
        match = _COMPARATOR_RE.fullmatch(comparator)
        if match is None:
            raise ValueError(f"invalid version comparator {comparator!r} in {version_range!r}")
        op, bound_text = match.groups()
        result = _COMPARE[op](current, SemVer.parse(bound_text)) and result
    return result


_COMPARE: dict[str, Callable[[SemVer, SemVer], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
}


# --- Checks -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckObservation:
    """What a :class:`Check` observed: pass/fail plus adapter-defined facts.

    ``observed`` becomes ``evaluation.completed.checks[].observed`` and can be
    cited as evidence; it must be a JSON object. A check never judges beyond
    the facts it observes (ADR-0013).
    """

    passed: bool
    observed: JsonObject


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckResult:
    """A check observation labelled with the check's adapter item id.

    ``to_dict()`` is the JSON form recorded with a check run.
    """

    check_id: str
    passed: bool
    observed: JsonObject

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "observed": to_plain_object(self.observed),
        }


class Check(Protocol):
    """A deterministic observation of lab facts (ADR-0009, ADR-0013)."""

    def validate_params(self, params: JsonObject) -> None:
        """Raise :class:`AdapterParamsError` unless ``params`` (the pack's
        ``checks[].params``) are valid for this check. Called at import."""
        ...

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        """Observe the running lab, using only ``lab.exec`` (inside the sandbox).

        ``params`` have passed :meth:`validate_params`. A failed or timed-out
        command is an observation (``passed=False``), not an error; errors
        from the Port (e.g. ``LabNotFoundError``) propagate. Timeouts and
        output limits come from ``params`` or the adapter; there are no
        harness defaults.
        """
        ...


# --- Fixture providers ------------------------------------------------------


class FixtureProvider(Protocol):
    """Domain-specific part of a lab: EnvironmentDefinition -> :class:`LabSpec`.

    Lab start, reset and destroy themselves are done by core through
    :class:`~harness.core.ports.LabRuntime`; reset calls :meth:`build_lab_spec`
    again, so it must be deterministic. The provenance ``fixture_id`` recorded
    on ``lab.started`` is the adapter item id from the environment's
    ``fixture`` field; the adapter version is on ``session.started``.
    """

    def validate_params(self, params: JsonObject) -> None:
        """Raise :class:`AdapterParamsError` unless ``params`` (the
        environment's ``params``) are valid. Called at import."""
        ...

    def build_lab_spec(self, image: ImageRef, params: JsonObject) -> LabSpec:
        """Return the spec for one lab from the environment's pinned ``image``
        and validated ``params``. Must use ``image`` as given."""
        ...


# --- Tools ------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalLaunch:
    """The program a terminal tool runs on the PTY inside the lab.

    Maps onto the ``argv`` / ``env`` / ``workdir`` of
    :class:`~harness.core.ports.TerminalOpenRequest`.
    """

    argv: Argv
    env: Mapping[str, str]
    workdir: str | None

    def __post_init__(self) -> None:
        check_argv(self.argv)


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectedCommand:
    """A command the learner completed, as ``terminal.command`` records it
    (``command``, ``cwd``). The caller assigns ``sequence`` and the event."""

    command: str
    cwd: str | None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must not be empty")
        if self.cwd == "":
            raise ValueError("cwd must be None or non-empty")


class CommandDetector(Protocol):
    """Per-session state machine finding command boundaries in PTY traffic.

    The terminal bridge caller feeds it every chunk in order, as it is
    written to or read from the PTY; chunks may split anywhere, including
    inside a multi-byte character or an escape sequence. It only interprets
    bytes; it never executes anything.
    """

    def feed_input(self, data: bytes) -> Sequence[DetectedCommand]:
        """Consume learner input bytes; return commands completed by them."""
        ...

    def feed_output(self, data: bytes) -> Sequence[DetectedCommand]:
        """Consume PTY output bytes; return commands completed by them."""
        ...


class TerminalTool(Protocol):
    """Terminal tool adapter: how to start the shell and how to read it."""

    def launch(self) -> TerminalLaunch:
        """The program to run on a new terminal in the lab (e.g. a login shell)."""
        ...

    def new_command_detector(self) -> CommandDetector:
        """A fresh detector for one terminal session."""
        ...


# --- Domain adapter ---------------------------------------------------------


class DomainAdapter(Protocol):
    """A versioned domain adapter package (shipped by an app).

    Item mappings are keyed by the item name, without the ``'<adapter_id>.'``
    prefix. v0.1 has one tool kind (terminal); another kind will widen the
    ``tools`` value type when a pack needs it.
    """

    @property
    def adapter_id(self) -> str: ...

    @property
    def version(self) -> str:
        """SemVer 2.0.0; recorded on ``session.started`` (ADR-0010)."""
        ...

    @property
    def fixtures(self) -> Mapping[str, FixtureProvider]: ...

    @property
    def checks(self) -> Mapping[str, Check]: ...

    @property
    def tools(self) -> Mapping[str, TerminalTool]: ...


# --- Registry ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemReference:
    """One adapter item a pack references, for :meth:`DomainAdapterRegistry.verify`.

    ``params`` is ``None`` where the pack gives none (tools, a Template's
    ``allowed_fixtures`` / ``allowed_checks``); then params are not validated.
    ``source`` locates the reference for the error report (e.g. file path and
    JSON pointer); it is not interpreted.
    """

    kind: ItemKind
    item_id: str
    params: JsonObject | None
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceProblem:
    """Why a pack's adapter declaration or reference is not acceptable.

    ``subject`` is the adapter id (adapter-level codes) or the item id.
    ``source`` is the reference's ``source``, or ``None`` for manifest-level
    problems.
    """

    code: ProblemCode
    subject: str
    source: str | None
    message: str


@dataclass(slots=True)
class DomainAdapterRegistry:
    """Registered domain adapters, one version per adapter id.

    Wiring (``harness/api``, ``harness/cli``) registers the adapters; core
    never imports them. Adapter ids may contain dots, so registration rejects
    an id that is a dotted prefix of another (``'a'`` and ``'a.b'``), which
    keeps every ``'<adapter_id>.<name>'`` split unambiguous.
    """

    _adapters: dict[str, DomainAdapter] = field(default_factory=dict)

    def register(self, adapter: DomainAdapter) -> None:
        """Add ``adapter``. Raises ``ValueError`` on an invalid or conflicting
        id, a non-SemVer version, or an invalid item name."""
        adapter_id = adapter.adapter_id
        if not _ADAPTER_ID_RE.fullmatch(adapter_id):
            raise ValueError(f"invalid adapter id {adapter_id!r}")
        SemVer.parse(adapter.version)
        for other in self._adapters:
            if other == adapter_id:
                raise ValueError(f"adapter {adapter_id!r} is already registered")
            if other.startswith(adapter_id + ".") or adapter_id.startswith(other + "."):
                raise ValueError(f"adapter ids {adapter_id!r} and {other!r} are ambiguous")
        for items in (adapter.fixtures, adapter.checks, adapter.tools):
            for name in items:
                if not name or not _ITEM_ID_RE.fullmatch(f"{adapter_id}.{name}"):
                    raise ValueError(f"invalid item name {name!r} in adapter {adapter_id!r}")
        self._adapters[adapter_id] = adapter

    def adapter(self, adapter_id: str) -> DomainAdapter:
        """Return the registered adapter. Raises ``KeyError`` if unknown."""
        return self._adapters[adapter_id]

    def split_item_id(self, item_id: str) -> tuple[str, str] | None:
        """Split into ``(adapter_id, name)`` against registered ids, or ``None``."""
        for adapter_id in self._adapters:
            prefix = adapter_id + "."
            if item_id.startswith(prefix) and len(item_id) > len(prefix):
                return adapter_id, item_id[len(prefix) :]
        return None

    def _resolve(self, kind: ItemKind, item_id: str) -> tuple[DomainAdapter, str]:
        split = self.split_item_id(item_id)
        if split is not None:
            adapter = self._adapters[split[0]]
            items: Mapping[str, object] = {
                "fixture": adapter.fixtures,
                "check": adapter.checks,
                "tool": adapter.tools,
            }[kind]
            if split[1] in items:
                return adapter, split[1]
        raise UnregisteredItemError(kind, item_id)

    def fixture(self, item_id: str) -> FixtureProvider:
        """Resolve a fixture provider. Raises :class:`UnregisteredItemError`."""
        adapter, name = self._resolve("fixture", item_id)
        return adapter.fixtures[name]

    def check(self, item_id: str) -> Check:
        """Resolve a check. Raises :class:`UnregisteredItemError`."""
        adapter, name = self._resolve("check", item_id)
        return adapter.checks[name]

    def tool(self, item_id: str) -> TerminalTool:
        """Resolve a tool. Raises :class:`UnregisteredItemError`."""
        adapter, name = self._resolve("tool", item_id)
        return adapter.tools[name]

    def verify(
        self, requirements: Mapping[str, str], references: Iterable[ItemReference]
    ) -> list[ReferenceProblem]:
        """Check a pack against the registry; an empty list means acceptable.

        ``requirements`` is the manifest's ``domain_adapters`` (id -> range).
        Each declared adapter must be registered with a version in range. Each
        reference must name an item of its kind in a declared, acceptable
        adapter, and its ``params`` (when given) must pass the item's
        ``validate_params``. The Importer refuses the pack on any problem
        (ADR-0009).
        """
        problems: list[ReferenceProblem] = []
        acceptable: set[str] = set()
        for adapter_id, version_range in sorted(requirements.items()):
            problem = self._check_requirement(adapter_id, version_range)
            if problem is None:
                acceptable.add(adapter_id)
            else:
                problems.append(problem)
        for ref in references:
            problem = self._check_reference(ref, requirements, acceptable)
            if problem is not None:
                problems.append(problem)
        return problems

    def _check_requirement(self, adapter_id: str, version_range: str) -> ReferenceProblem | None:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            return ReferenceProblem(
                code="adapter_not_registered",
                subject=adapter_id,
                source=None,
                message=f"domain adapter {adapter_id!r} is not registered",
            )
        try:
            ok = version_satisfies(adapter.version, version_range)
        except ValueError as exc:
            return ReferenceProblem(
                code="invalid_version_range", subject=adapter_id, source=None, message=str(exc)
            )
        if not ok:
            return ReferenceProblem(
                code="version_out_of_range",
                subject=adapter_id,
                source=None,
                message=(
                    f"domain adapter {adapter_id!r} is version {adapter.version}, "
                    f"outside {version_range!r}"
                ),
            )
        return None

    def _check_reference(
        self, ref: ItemReference, requirements: Mapping[str, str], acceptable: set[str]
    ) -> ReferenceProblem | None:
        rejected = (a for a in requirements if a not in acceptable)
        if any(ref.item_id.startswith(a + ".") for a in rejected):
            return None  # its adapter is already reported at the adapter level
        split = self.split_item_id(ref.item_id)
        if split is not None and split[0] not in requirements:
            return ReferenceProblem(
                code="adapter_not_declared",
                subject=ref.item_id,
                source=ref.source,
                message=f"{ref.kind} {ref.item_id!r} belongs to adapter {split[0]!r}, "
                "which the manifest does not declare",
            )
        try:
            if ref.kind == "fixture":
                validate = self.fixture(ref.item_id).validate_params
            elif ref.kind == "check":
                validate = self.check(ref.item_id).validate_params
            else:
                self.tool(ref.item_id)
                validate = None
        except UnregisteredItemError as exc:
            return ReferenceProblem(
                code="item_not_registered", subject=ref.item_id, source=ref.source, message=str(exc)
            )
        if ref.params is not None and validate is not None:
            try:
                validate(ref.params)
            except AdapterParamsError as exc:
                return ReferenceProblem(
                    code="invalid_params",
                    subject=ref.item_id,
                    source=ref.source,
                    message=str(exc),
                )
        return None

    def adapter_versions(self, requirements: Mapping[str, str]) -> dict[str, str]:
        """Adapter id -> registered version for the declared adapters, for
        ``session.started`` provenance. Raises ``KeyError`` if one is unknown."""
        return {adapter_id: self._adapters[adapter_id].version for adapter_id in requirements}


def run_check(
    registry: DomainAdapterRegistry,
    check_id: str,
    params: JsonObject,
    *,
    lab: LabRuntime,
    lab_instance_id: str,
) -> CheckResult:
    """Resolve ``check_id``, run it against the lab and label the result."""
    observation = registry.check(check_id).run(lab, lab_instance_id, params)
    return CheckResult(check_id=check_id, passed=observation.passed, observed=observation.observed)
