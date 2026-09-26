"""Building the real objects the commands and routes run against (ADR-0017: the CLI wires).

Core owns the Ports and the registries; ``harness/adapters/*`` (and an app's
domain adapters) implement them; nothing down there knows which implementation is
in use. This module is where the choice is made for the CLI:
the v2 Postgres event store from ``DATABASE_URL``, the
litellm LLM provider (the pack's model string picks the provider; litellm reads
the matching API key from the environment). Domain
adapters belong to apps, so the CLI registers none.

Every failure to build one of them is a :class:`~harness.cli.errors.CommandError`,
so a missing key reads as a message rather than a traceback.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from harness.adapters.fake_llm import FakeDevLLMProvider
from harness.adapters.litellm import LiteLLMProvider
from harness.adapters.postgres.engine import create_engine_from_env, get_database_url
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.cli.errors import CommandError
from harness.core.contract_schemas import ContractSchemas, ContractsNotFoundError
from harness.core.domain_adapter import DomainAdapterRegistry
from harness.core.ports.events_v2 import EventStoreV2
from harness.core.registry import AlgorithmRegistry
from harness.core.registry.builtin import v01_algorithm_registry
from harness.core.settings import Settings

logger = logging.getLogger(__name__)


def contract_schemas() -> ContractSchemas:
    try:
        return ContractSchemas.load()
    except ContractsNotFoundError as exc:
        raise CommandError(str(exc)) from exc


def domain_adapters() -> DomainAdapterRegistry:
    """No domain adapters: they belong to apps, which register their own."""
    return DomainAdapterRegistry()


def algorithms() -> AlgorithmRegistry:
    return v01_algorithm_registry()


def database_url() -> str:
    return get_database_url()


def llm_provider() -> LiteLLMProvider | FakeDevLLMProvider:
    """The one LLM adapter; the pack's model string decides the provider.

    Credentials stay in the environment (``OPENAI_API_KEY``,
    ``ANTHROPIC_API_KEY``, ...), where litellm reads them itself.

    ``MORPHLOOP_LLM_PROVIDER=fake`` (#130) swaps in a deterministic, keyless
    fake for dev-stack / local E2E runs; refused when ``MORPHLOOP_ENVIRONMENT``
    is ``production``, since it never calls a real model.
    """
    settings = Settings()
    if settings.morphloop_llm_provider == "fake":
        if settings.morphloop_environment == "production":
            raise CommandError(
                "MORPHLOOP_LLM_PROVIDER=fake is refused when MORPHLOOP_ENVIRONMENT=production: "
                "it fabricates output instead of calling a real model."
            )
        logger.warning(
            "MORPHLOOP_LLM_PROVIDER=fake: LLM output is a deterministic fake, not a real "
            "model. Dev-stack / local E2E only."
        )
        return FakeDevLLMProvider()
    return LiteLLMProvider()


@contextmanager
def event_store_v2() -> Iterator[EventStoreV2]:
    """The v2 Postgres event store built from ``DATABASE_URL``; disposes the engine."""
    engine = create_engine_from_env()
    try:
        yield PostgresEventStoreV2(engine, contract_schemas())
    finally:
        engine.dispose()
