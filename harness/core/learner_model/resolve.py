"""Build the learner model a pack selected, from the pack projection."""

from __future__ import annotations

from harness.core.contract_schemas import ContractSchemas
from harness.core.learner_model.types import LearnerModel
from harness.core.pack.catalog import PackCatalog
from harness.core.pack.model import PackRef
from harness.core.ports import LLMProvider
from harness.core.registry.algorithms import (
    LEARNER_MODEL_ROLE,
    AlgorithmRegistry,
    LearnerModelDependencies,
)


def load_learner_model(
    *,
    catalog: PackCatalog,
    pack: PackRef,
    registry: AlgorithmRegistry,
    llm: LLMProvider,
    schemas: ContractSchemas,
) -> LearnerModel:
    """Resolve ``registry.learner_model`` of ``pack`` with its prompt and parameters."""
    selection = catalog.get_pack(pack).registry[LEARNER_MODEL_ROLE]
    prompt = catalog.get_prompt(pack, selection.llm.prompt_id, selection.llm.prompt_version)
    return registry.learner_model(
        selection, LearnerModelDependencies(llm=llm, schemas=schemas, prompt=prompt.text)
    )
