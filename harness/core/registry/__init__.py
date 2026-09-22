"""Algorithm registry (ADR-0004, ADR-0013).

``algorithms`` holds the registry and the parsed pack selection; ``builtin``
builds the v0.1 registry (``llm-learner-model@0.1.0`` for ``learner_model``).
``builtin`` is not re-exported here, to keep this package free of an import
cycle with ``harness.core.learner_model.llm``.
"""

from harness.core.registry.algorithms import (
    LEARNER_MODEL_ROLE,
    AlgorithmError,
    AlgorithmRegistry,
    LearnerModelDependencies,
    LearnerModelFactory,
    RegistrySelection,
    SelectionError,
    UnknownImplementationError,
)

__all__ = [
    "LEARNER_MODEL_ROLE",
    "AlgorithmError",
    "AlgorithmRegistry",
    "LearnerModelDependencies",
    "LearnerModelFactory",
    "RegistrySelection",
    "SelectionError",
    "UnknownImplementationError",
]
