"""The v0.1 algorithm registry: one learner model, the LLM implementation (ADR-0012, ADR-0013)."""

from __future__ import annotations

from harness.core.learner_model.llm import IMPLEMENTATION, LLMLearnerModelFactory
from harness.core.registry.algorithms import AlgorithmRegistry


def v01_algorithm_registry() -> AlgorithmRegistry:
    """A registry with ``llm-learner-model@0.1.0`` registered for ``learner_model``."""
    registry = AlgorithmRegistry()
    registry.register_learner_model(IMPLEMENTATION, LLMLearnerModelFactory())
    return registry
