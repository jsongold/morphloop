"""Pack-declared options of the roles the loop drives (ADR-0002, ADR-0004).

The harness holds no defaults: a missing option is an error, never a fallback.
v0.1 drives three roles from here, ``evaluator``, ``tutor`` and the optional
``memo_summarizer``; ``learner_model`` is resolved through the algorithm
registry (:func:`harness.core.learner_model.resolve.load_learner_model`).

Options honoured (``contents/<pack>/manifest.json``, ``registry.<role>.options``):

``evaluator.include_reference_solution``
    whether the evaluator, which runs after the learner submitted, is given the
    activity's reference solution. It is never given to the tutor (AC-J6).
``tutor.modes``
    every reply mode the pack allows.
``tutor.default_mode_unfinished_attempt`` / ``tutor.default_mode_finished_attempt``
    the mode the harness picks when the learner asks for none.
``tutor.modes_allowed_unfinished_attempt``
    the modes a learner may ask for while an attempt is unfinished; anything
    else falls back to the unfinished default (AC-E3).
``tutor.recent_events_limit``
    how many recent session events go into the tutor context (AC-E2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from harness.core.ports import JsonObject, JsonValue
from harness.core.registry.algorithms import RegistrySelection

EVALUATOR_ROLE = "evaluator"
TUTOR_ROLE = "tutor"
MEMO_SUMMARIZER_ROLE = "memo_summarizer"
EVALUATOR_IMPLEMENTATION = "llm-evaluator@0.1.0"
TUTOR_IMPLEMENTATION = "llm-tutor@0.1.0"
MEMO_SUMMARIZER_IMPLEMENTATION = "llm-memo-summarizer@0.1.0"
EVALUATOR_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/llm/evaluator.judgment/1.json"
TUTOR_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/llm/tutor.reply/1.json"
MEMO_SUMMARIZER_SCHEMA_ID = "https://morphloop.dev/contracts/schemas/llm/memo_summarizer.note/1.json"

_EVALUATOR_OPTIONS = frozenset({"include_reference_solution"})
_TUTOR_OPTIONS = frozenset(
    {
        "modes",
        "default_mode_unfinished_attempt",
        "default_mode_finished_attempt",
        "modes_allowed_unfinished_attempt",
        "recent_events_limit",
    }
)
_MEMO_SUMMARIZER_OPTIONS: frozenset[str] = frozenset()


class PackOptionError(ValueError):
    """A pack option is missing or unusable (the harness has no default)."""


def _options(selection: RegistrySelection, known: frozenset[str]) -> Mapping[str, JsonValue]:
    where = f"registry.{selection.role}.options"
    missing = sorted(known - set(selection.options))
    if missing:
        raise PackOptionError(f"{where}: missing {missing}")
    unknown = sorted(set(selection.options) - known)
    if unknown:
        raise PackOptionError(f"{where}: unknown {unknown}")
    return selection.options


def _boolean(options: Mapping[str, JsonValue], name: str, where: str) -> bool:
    value = options[name]
    if not isinstance(value, bool):
        raise PackOptionError(f"{where}.{name} must be a boolean")
    return value


def _positive_int(options: Mapping[str, JsonValue], name: str, where: str) -> int:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PackOptionError(f"{where}.{name} must be a positive integer")
    return value


def _token(options: Mapping[str, JsonValue], name: str, where: str) -> str:
    value = options[name]
    if not isinstance(value, str) or not value:
        raise PackOptionError(f"{where}.{name} must be a non-empty string")
    return value


def _tokens(options: Mapping[str, JsonValue], name: str, where: str) -> tuple[str, ...]:
    value = options[name]
    if (
        isinstance(value, str | Mapping)
        or not isinstance(value, Sequence)
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise PackOptionError(f"{where}.{name} must be an array of non-empty strings")
    return tuple(str(item) for item in value)


def _check_implementation(selection: RegistrySelection, expected: str) -> None:
    if selection.implementation != expected:
        raise PackOptionError(
            f"registry.{selection.role}.implementation {selection.implementation!r} "
            f"is not registered; v0.1 has {expected!r}"
        )


def _check_output_schema(selection: RegistrySelection, expected: str) -> None:
    if selection.output_schema != expected:
        raise PackOptionError(
            f"registry.{selection.role}.output_schema must be {expected!r}, "
            f"got {selection.output_schema!r}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatorOptions:
    """Parsed ``registry.evaluator`` of one pack."""

    selection: RegistrySelection
    include_reference_solution: bool

    @property
    def context_budget_tokens(self) -> int:
        return self.selection.context_budget_tokens

    @classmethod
    def parse(cls, selection: RegistrySelection) -> EvaluatorOptions:
        _check_implementation(selection, EVALUATOR_IMPLEMENTATION)
        _check_output_schema(selection, EVALUATOR_SCHEMA_ID)
        where = f"registry.{selection.role}.options"
        options = _options(selection, _EVALUATOR_OPTIONS)
        return cls(
            selection=selection,
            include_reference_solution=_boolean(options, "include_reference_solution", where),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TutorOptions:
    """Parsed ``registry.tutor`` of one pack."""

    selection: RegistrySelection
    modes: tuple[str, ...]
    default_mode_unfinished_attempt: str
    default_mode_finished_attempt: str
    modes_allowed_unfinished_attempt: tuple[str, ...]
    recent_events_limit: int

    @property
    def context_budget_tokens(self) -> int:
        return self.selection.context_budget_tokens

    @classmethod
    def parse(cls, selection: RegistrySelection) -> TutorOptions:
        _check_implementation(selection, TUTOR_IMPLEMENTATION)
        _check_output_schema(selection, TUTOR_SCHEMA_ID)
        where = f"registry.{selection.role}.options"
        options = _options(selection, _TUTOR_OPTIONS)
        modes = _tokens(options, "modes", where)
        if not modes:
            raise PackOptionError(f"{where}.modes must not be empty")
        allowed = _tokens(options, "modes_allowed_unfinished_attempt", where)
        unfinished = _token(options, "default_mode_unfinished_attempt", where)
        finished = _token(options, "default_mode_finished_attempt", where)
        for name, value in (
            ("default_mode_unfinished_attempt", unfinished),
            ("default_mode_finished_attempt", finished),
        ):
            if value not in modes:
                raise PackOptionError(f"{where}.{name} {value!r} is not in modes {list(modes)}")
        outside = [mode for mode in allowed if mode not in modes]
        if outside:
            raise PackOptionError(
                f"{where}.modes_allowed_unfinished_attempt {outside} are not in modes"
            )
        if unfinished not in allowed:
            raise PackOptionError(
                f"{where}.default_mode_unfinished_attempt {unfinished!r} must also be in "
                "modes_allowed_unfinished_attempt"
            )
        return cls(
            selection=selection,
            modes=modes,
            default_mode_unfinished_attempt=unfinished,
            default_mode_finished_attempt=finished,
            modes_allowed_unfinished_attempt=allowed,
            recent_events_limit=_positive_int(options, "recent_events_limit", where),
        )

    def resolve_mode(self, requested: str | None, *, attempt_unfinished: bool) -> str:
        """The mode the server uses; the learner's request never overrides AC-E3."""
        if requested is not None and requested not in self.modes:
            raise PackOptionError(
                f"requested_mode {requested!r} is not declared by the pack {list(self.modes)}"
            )
        if attempt_unfinished:
            if requested is not None and requested in self.modes_allowed_unfinished_attempt:
                return requested
            return self.default_mode_unfinished_attempt
        return requested if requested is not None else self.default_mode_finished_attempt


def role_selection(registry: Mapping[str, RegistrySelection], role: str) -> RegistrySelection:
    """The pack's selection for ``role``; a pack without it cannot run the loop."""
    selection = registry.get(role)
    if selection is None:
        raise PackOptionError(f"the pack declares no registry.{role}")
    return selection


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoSummarizerOptions:
    """Parsed ``registry.memo_summarizer`` of one pack."""

    selection: RegistrySelection

    @property
    def context_budget_tokens(self) -> int:
        return self.selection.context_budget_tokens

    @classmethod
    def parse(cls, selection: RegistrySelection) -> MemoSummarizerOptions:
        _check_implementation(selection, MEMO_SUMMARIZER_IMPLEMENTATION)
        _check_output_schema(selection, MEMO_SUMMARIZER_SCHEMA_ID)
        _options(selection, _MEMO_SUMMARIZER_OPTIONS)
        return cls(selection=selection)


def memo_summarizer_options(
    registry: Mapping[str, RegistrySelection],
) -> MemoSummarizerOptions | None:
    """The pack's summarizer selection, or ``None`` when the pack records no memos."""
    selection = registry.get(MEMO_SUMMARIZER_ROLE)
    if selection is None:
        return None
    return MemoSummarizerOptions.parse(selection)


def evaluator_options(registry: Mapping[str, RegistrySelection]) -> EvaluatorOptions:
    return EvaluatorOptions.parse(role_selection(registry, EVALUATOR_ROLE))


def tutor_options(registry: Mapping[str, RegistrySelection]) -> TutorOptions:
    return TutorOptions.parse(role_selection(registry, TUTOR_ROLE))


def registry_implementations(registry: Mapping[str, RegistrySelection]) -> dict[str, JsonValue]:
    """``role -> 'name@version'`` for ``session.started`` provenance (ADR-0010)."""
    return {role: selection.implementation for role, selection in sorted(registry.items())}


def as_json_object(document: JsonObject) -> JsonObject:
    """Identity helper kept for readability at call sites."""
    return document
