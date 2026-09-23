"""Pack-declared options of the evaluator and tutor roles (ADR-0002, ADR-0004).

The harness holds no defaults: a missing option is an error. The last test
parses the options of the real pack under ``contents/software-engineering`` with
the same code the loop uses, so the two cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from loop_harness import PACK_LOCATION, pack_files

from harness.core.loop import EvaluatorOptions, PackOptionError, TutorOptions
from harness.core.registry.algorithms import RegistrySelection

CONTENTS = Path(__file__).resolve().parents[2] / "contents" / "software-engineering"


def _registry(role: str) -> dict[str, Any]:
    manifest = json.loads(pack_files()["manifest.json"])
    selection: dict[str, Any] = manifest["registry"][role]
    return selection


def _tutor(**overrides: Any) -> TutorOptions:
    raw = _registry("tutor")
    raw["options"] = {**raw["options"], **overrides}
    return TutorOptions.parse(RegistrySelection.parse("tutor", raw))


def test_evaluator_options_are_read_from_the_pack() -> None:
    options = EvaluatorOptions.parse(RegistrySelection.parse("evaluator", _registry("evaluator")))
    assert options.include_reference_solution is True
    assert options.context_budget_tokens == 48000


def test_a_missing_option_is_an_error_not_a_default() -> None:
    raw = _registry("evaluator")
    raw["options"] = {}
    with pytest.raises(PackOptionError, match="include_reference_solution"):
        EvaluatorOptions.parse(RegistrySelection.parse("evaluator", raw))

    raw = _registry("tutor")
    raw["options"] = {k: v for k, v in raw["options"].items() if k != "recent_events_limit"}
    with pytest.raises(PackOptionError, match="recent_events_limit"):
        TutorOptions.parse(RegistrySelection.parse("tutor", raw))


def test_an_unknown_option_is_refused() -> None:
    with pytest.raises(PackOptionError, match="unknown"):
        _tutor(temperature_of_voice="warm")


def test_an_unregistered_implementation_is_refused() -> None:
    raw = _registry("tutor")
    raw["implementation"] = "handwritten-tutor@2.0.0"
    with pytest.raises(PackOptionError, match="not registered"):
        TutorOptions.parse(RegistrySelection.parse("tutor", raw))


def test_defaults_must_be_declared_modes() -> None:
    with pytest.raises(PackOptionError, match="default_mode_finished_attempt"):
        _tutor(default_mode_finished_attempt="summary")
    with pytest.raises(PackOptionError, match="modes_allowed_unfinished_attempt"):
        _tutor(default_mode_unfinished_attempt="review")


def test_mode_resolution_follows_the_pack() -> None:
    options = _tutor()
    assert options.resolve_mode(None, attempt_unfinished=True) == "hint"
    assert options.resolve_mode(None, attempt_unfinished=False) == "review"
    assert options.resolve_mode("explain", attempt_unfinished=True) == "explain"
    assert options.resolve_mode("review", attempt_unfinished=True) == "hint"
    assert options.resolve_mode("review", attempt_unfinished=False) == "review"
    with pytest.raises(PackOptionError):
        options.resolve_mode("jailbreak", attempt_unfinished=True)


def test_the_test_pack_location_is_self_contained() -> None:
    assert PACK_LOCATION.startswith("memory://")


@pytest.mark.skipif(not CONTENTS.is_dir(), reason="the software-engineering pack is not present")
def test_the_real_pack_options_parse() -> None:
    manifest = json.loads((CONTENTS / "manifest.json").read_text(encoding="utf-8"))
    registry = manifest["registry"]
    evaluator = EvaluatorOptions.parse(RegistrySelection.parse("evaluator", registry["evaluator"]))
    tutor = TutorOptions.parse(RegistrySelection.parse("tutor", registry["tutor"]))
    assert evaluator.include_reference_solution is True
    assert tutor.recent_events_limit == 30
    assert tutor.resolve_mode("review", attempt_unfinished=True) == "hint"
    assert tutor.resolve_mode(None, attempt_unfinished=False) == "review"
