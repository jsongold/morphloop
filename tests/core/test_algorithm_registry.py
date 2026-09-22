"""Tests for the algorithm registry (harness.core.registry) and pack selection parsing.

The selection comes from the contract DNS example pack manifest; the harness adds
no defaults, so every missing parameter must be an error (ADR-0002, ADR-0004).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from harness.core.contract_schemas import ContractSchemas
from harness.core.learner_model.llm import IMPLEMENTATION, LLMLearnerModel, LLMLearnerModelFactory
from harness.core.registry import (
    AlgorithmRegistry,
    LearnerModelDependencies,
    RegistrySelection,
    SelectionError,
    UnknownImplementationError,
)
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import FakeLLMProvider

MANIFEST = (
    Path(__file__).resolve().parent.parent / "contracts/fixtures/pack/valid/dns-pack/manifest.json"
)
SCHEMAS = ContractSchemas.load()


def _raw() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(MANIFEST.read_text())["registry"]["learner_model"]
    return copy.deepcopy(raw)


def _deps() -> LearnerModelDependencies:
    return LearnerModelDependencies(llm=FakeLLMProvider(), schemas=SCHEMAS, prompt="Update.")


def test_resolves_the_pack_selected_learner_model() -> None:
    selection = RegistrySelection.parse("learner_model", _raw())
    assert selection.implementation == IMPLEMENTATION
    assert selection.llm.prompt_id == "learner-model-update"
    assert selection.llm.generation_parameters == {"temperature": 0, "max_tokens": 4000}
    assert selection.context_budget_tokens == 8000
    model = v01_algorithm_registry().learner_model(selection, _deps())
    assert isinstance(model, LLMLearnerModel)


def test_v01_registry_governs_only_the_learner_model() -> None:
    registry = v01_algorithm_registry()
    assert registry.governed_roles() == {"learner_model"}
    assert registry.implementations("learner_model") == {IMPLEMENTATION}
    assert registry.implementations("evaluator") == frozenset()


@pytest.mark.parametrize(
    "path",
    [
        ("implementation",),
        ("llm",),
        ("llm", "model"),
        ("llm", "prompt_version"),
        ("llm", "generation_parameters"),
        ("output_schema",),
        ("context_budget_tokens",),
        ("options",),
    ],
    ids=lambda p: ".".join(p),
)
def test_missing_selection_parameter_is_an_error(path: tuple[str, ...]) -> None:
    raw = _raw()
    target = raw
    for name in path[:-1]:
        target = target[name]
    del target[path[-1]]
    with pytest.raises(SelectionError, match=path[-1]):
        RegistrySelection.parse("learner_model", raw)


def test_tutor_may_omit_output_schema_but_judgment_roles_may_not() -> None:
    raw = _raw()
    del raw["output_schema"]
    assert RegistrySelection.parse("tutor", raw).output_schema is None
    with pytest.raises(SelectionError):
        RegistrySelection.parse("evaluator", raw)


def test_missing_implementation_option_is_an_error() -> None:
    raw = _raw()
    raw["options"] = {}
    selection = RegistrySelection.parse("learner_model", raw)
    registry = v01_algorithm_registry()
    with pytest.raises(SelectionError, match="max_mastery_delta"):
        registry.validate(selection, SCHEMAS)
    with pytest.raises(SelectionError, match="max_mastery_delta"):
        registry.learner_model(selection, _deps())


@pytest.mark.parametrize(
    "options",
    [{"max_mastery_delta": 0}, {"max_mastery_delta": 1.5}, {"max_mastery_delta": "0.3"}],
)
def test_invalid_or_unknown_options_are_errors(options: dict[str, Any]) -> None:
    raw = _raw()
    raw["options"] = options
    with pytest.raises(SelectionError):
        v01_algorithm_registry().validate(RegistrySelection.parse("learner_model", raw), SCHEMAS)
    raw["options"] = {"max_mastery_delta": 0.3, "extra": 1}
    with pytest.raises(SelectionError, match="unknown"):
        v01_algorithm_registry().validate(RegistrySelection.parse("learner_model", raw), SCHEMAS)


def test_wrong_output_schema_is_an_error() -> None:
    raw = _raw()
    raw["output_schema"] = "https://morphloop.dev/contracts/schemas/llm/tutor.reply/1.json"
    with pytest.raises(SelectionError, match="learner_model.update"):
        v01_algorithm_registry().validate(RegistrySelection.parse("learner_model", raw), SCHEMAS)


def test_unknown_implementation_is_an_error() -> None:
    raw = _raw()
    raw["implementation"] = "bkt-lite@1.0.0"
    selection = RegistrySelection.parse("learner_model", raw)
    with pytest.raises(UnknownImplementationError):
        v01_algorithm_registry().learner_model(selection, _deps())
    with pytest.raises(SelectionError):
        v01_algorithm_registry().validate(RegistrySelection.parse("evaluator", _raw()), SCHEMAS)


def test_registration_rules() -> None:
    registry = AlgorithmRegistry()
    with pytest.raises(ValueError):
        registry.register_learner_model("no-version", LLMLearnerModelFactory())
    registry.register_learner_model("x@1", LLMLearnerModelFactory())
    with pytest.raises(ValueError):
        registry.register_learner_model("x@1", LLMLearnerModelFactory())
