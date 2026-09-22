"""Algorithm registry: ``name@version`` -> implementation factory (ADR-0004, ADR-0013).

A pack selects an implementation per role in ``manifest.registry`` and declares
every parameter (``pack/manifest.json#/$defs/llm_selection``). The harness adds
no defaults (ADR-0002): a missing parameter is an error, raised either by
:meth:`RegistrySelection.parse` (the shared selection fields) or by the
factory's ``validate`` (implementation ``options``).

v0.1 governs one role, ``learner_model`` (ADR-0012). The other roles a manifest
may declare (``evaluator``, ``generator``, ``tutor``, ...) are parsed but not
resolved here yet; :meth:`AlgorithmRegistry.governed_roles` tells the Importer
which selections it must verify.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from harness.core.contract_schemas import ContractSchemas
from harness.core.learner_model.types import LearnerModel
from harness.core.ports import (
    GenerationParameter,
    JsonObject,
    JsonValue,
    LLMProvenance,
    LLMProvider,
)

LEARNER_MODEL_ROLE = "learner_model"
_SELECTION_FIELDS = frozenset(
    {"implementation", "llm", "output_schema", "context_budget_tokens", "options"}
)
_LLM_FIELDS = ("provider", "model", "prompt_id", "prompt_version", "generation_parameters")


class AlgorithmError(Exception):
    """Base class for algorithm registry failures."""


class SelectionError(AlgorithmError, ValueError):
    """A pack selection is missing a parameter or has an invalid one."""


class UnknownImplementationError(AlgorithmError, LookupError):
    """No implementation is registered under this role and ``name@version``."""

    def __init__(self, role: str, implementation: str) -> None:
        super().__init__(f"no {role} implementation {implementation!r} is registered")
        self.role = role
        self.implementation = implementation


def _require(raw: Mapping[str, JsonValue], name: str, where: str) -> JsonValue:
    if name not in raw:
        raise SelectionError(f"{where}: missing required parameter {name!r}")
    return raw[name]


def _string(value: JsonValue, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise SelectionError(f"{where} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class RegistrySelection:
    """One ``manifest.registry.<role>`` entry with every parameter."""

    role: str
    implementation: str
    llm: LLMProvenance
    output_schema: str | None
    context_budget_tokens: int
    options: JsonObject

    @classmethod
    def parse(cls, role: str, raw: JsonValue) -> RegistrySelection:
        """Parse a selection; raises :class:`SelectionError` on any missing or
        malformed field. ``output_schema`` may be absent only for ``tutor``."""
        where = f"registry.{role}"
        if not isinstance(raw, Mapping):
            raise SelectionError(f"{where} must be an object")
        unknown = sorted(set(raw) - _SELECTION_FIELDS)
        if unknown:
            raise SelectionError(f"{where}: unknown parameters {unknown}")
        implementation = _string(_require(raw, "implementation", where), f"{where}.implementation")
        if implementation.count("@") != 1:
            raise SelectionError(f"{where}.implementation must be 'name@version'")
        llm_raw = _require(raw, "llm", where)
        if not isinstance(llm_raw, Mapping):
            raise SelectionError(f"{where}.llm must be an object")
        for name in _LLM_FIELDS:
            _require(llm_raw, name, f"{where}.llm")
        params = llm_raw["generation_parameters"]
        if not isinstance(params, Mapping):
            raise SelectionError(f"{where}.llm.generation_parameters must be an object")
        generation: dict[str, GenerationParameter] = {}
        for key, value in params.items():
            if value is not None and not isinstance(value, str | int | float | bool):
                raise SelectionError(f"{where}.llm.generation_parameters.{key} must be a scalar")
            generation[key] = value
        llm = LLMProvenance(
            provider=_string(llm_raw["provider"], f"{where}.llm.provider"),
            model=_string(llm_raw["model"], f"{where}.llm.model"),
            prompt_id=_string(llm_raw["prompt_id"], f"{where}.llm.prompt_id"),
            prompt_version=_string(llm_raw["prompt_version"], f"{where}.llm.prompt_version"),
            generation_parameters=generation,
        )
        output_schema: str | None = None
        if role != "tutor" or "output_schema" in raw:
            output_schema = _string(_require(raw, "output_schema", where), f"{where}.output_schema")
        budget = _require(raw, "context_budget_tokens", where)
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            raise SelectionError(f"{where}.context_budget_tokens must be a positive integer")
        options = _require(raw, "options", where)
        if not isinstance(options, Mapping):
            raise SelectionError(f"{where}.options must be an object")
        return cls(
            role=role,
            implementation=implementation,
            llm=llm,
            output_schema=output_schema,
            context_budget_tokens=budget,
            options=options,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LearnerModelDependencies:
    """What a learner-model implementation is built with.

    ``prompt`` is the text of the pack prompt named by ``selection.llm``
    (resolved from the pack projection by the caller).
    """

    llm: LLMProvider
    schemas: ContractSchemas
    prompt: str


class LearnerModelFactory(Protocol):
    """Registered under ``name@version`` for role ``learner_model``."""

    def validate(self, selection: RegistrySelection, schemas: ContractSchemas) -> None:
        """Raise :class:`SelectionError` unless ``selection`` (notably ``options``
        and ``output_schema``) is acceptable. Called at import and at resolve."""
        ...

    def create(
        self, selection: RegistrySelection, deps: LearnerModelDependencies
    ) -> LearnerModel: ...


class AlgorithmRegistry:
    """Registered implementations per role; wiring builds one at startup."""

    def __init__(self) -> None:
        self._learner_models: dict[str, LearnerModelFactory] = {}

    def register_learner_model(self, implementation: str, factory: LearnerModelFactory) -> None:
        """Register ``implementation`` (``name@version``). Duplicates raise ``ValueError``."""
        if implementation.count("@") != 1 or not all(implementation.split("@")):
            raise ValueError(f"implementation must be 'name@version', got {implementation!r}")
        if implementation in self._learner_models:
            raise ValueError(f"learner_model {implementation!r} is already registered")
        self._learner_models[implementation] = factory

    def governed_roles(self) -> frozenset[str]:
        """Roles whose selections this registry resolves and the Importer verifies."""
        return frozenset({LEARNER_MODEL_ROLE})

    def implementations(self, role: str) -> frozenset[str]:
        if role == LEARNER_MODEL_ROLE:
            return frozenset(self._learner_models)
        return frozenset()

    def _learner_model_factory(self, selection: RegistrySelection) -> LearnerModelFactory:
        if selection.role != LEARNER_MODEL_ROLE:
            raise SelectionError(f"selection role is {selection.role!r}, not learner_model")
        factory = self._learner_models.get(selection.implementation)
        if factory is None:
            raise UnknownImplementationError(selection.role, selection.implementation)
        return factory

    def validate(self, selection: RegistrySelection, schemas: ContractSchemas) -> None:
        """Verify a selection of a governed role (import-time check).

        Raises :class:`UnknownImplementationError` or :class:`SelectionError`.
        """
        if selection.role not in self.governed_roles():
            raise SelectionError(f"role {selection.role!r} is not resolved by this registry")
        self._learner_model_factory(selection).validate(selection, schemas)

    def learner_model(
        self, selection: RegistrySelection, deps: LearnerModelDependencies
    ) -> LearnerModel:
        """Resolve and build the learner model the pack selected."""
        factory = self._learner_model_factory(selection)
        factory.validate(selection, deps.schemas)
        return factory.create(selection, deps)
