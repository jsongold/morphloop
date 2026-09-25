"""Building the real objects the commands run against (ADR-0017: the CLI wires).

Core owns the Ports and the registries; ``harness/adapters/*`` and
``domains/*`` implement them; nothing down there knows which implementation is
in use. This module is where the choice is made for the CLI: the local
filesystem pack source, the Postgres event store from ``DATABASE_URL``, the
litellm LLM provider (the pack's model string picks the provider; litellm reads
the matching API key from the environment), the Docker lab runtime, and the DNS
domain adapter (the v0.1 slice, ADR-0012).

Every failure to build one of them is a :class:`~harness.cli.errors.CommandError`,
so a missing key or an unreachable Docker daemon reads as a message rather
than a traceback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager

import domains.dns
from harness.adapters.docker_lab import DockerLabRuntime
from harness.adapters.fs_pack_source import FilesystemPackSource
from harness.adapters.litellm import LiteLLMProvider
from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.postgres.event_store import PostgresEventStore
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.cli.errors import CommandError
from harness.core.contract_schemas import ContractSchemas, ContractsNotFoundError
from harness.core.domain_adapter import DomainAdapterRegistry
from harness.core.pack import PackImporter
from harness.core.ports import (
    EventStore,
    EventTransaction,
    LabRuntime,
    LLMProvider,
    PackSource,
    StoredEvent,
)
from harness.core.ports.events_v2 import EventStoreV2
from harness.core.registry import AlgorithmRegistry
from harness.core.registry.builtin import v01_algorithm_registry

type ImporterFactory = Callable[[PackSource], PackImporter]


def contract_schemas() -> ContractSchemas:
    try:
        return ContractSchemas.load()
    except ContractsNotFoundError as exc:
        raise CommandError(str(exc)) from exc


def domain_adapters() -> DomainAdapterRegistry:
    """The domain adapters of the v0.1 slice (ADR-0012)."""
    registry = DomainAdapterRegistry()
    registry.register(domains.dns.adapter())
    return registry


def algorithms() -> AlgorithmRegistry:
    return v01_algorithm_registry()


def importer_factory(
    *, store: EventStore, schemas: ContractSchemas, adapters: DomainAdapterRegistry
) -> ImporterFactory:
    """A factory that builds a :class:`PackImporter` over a given pack source."""

    def build(source: PackSource) -> PackImporter:
        return PackImporter(
            source=source,
            store=store,
            schemas=schemas,
            adapters=adapters,
            algorithms=algorithms(),
        )

    return build


def pack_source() -> PackSource:
    return FilesystemPackSource()


def llm_provider() -> LLMProvider:
    """The one LLM adapter; the pack's model string decides the provider.

    Credentials stay in the environment (``OPENAI_API_KEY``,
    ``ANTHROPIC_API_KEY``, ...), where litellm reads them itself.
    """
    return LiteLLMProvider()


def lab_runtime() -> LabRuntime:
    import docker  # noqa: PLC0415 - only the commands that start a lab need the daemon

    try:
        client = docker.from_env()
    except docker.errors.DockerException as exc:
        raise CommandError(f"cannot reach the Docker daemon: {exc}") from exc
    return DockerLabRuntime(client)


@contextmanager
def event_store() -> Iterator[EventStore]:
    """The Postgres event store built from ``DATABASE_URL``; disposes the engine."""
    engine = create_engine_from_env()
    try:
        yield PostgresEventStore(engine)
    finally:
        engine.dispose()


@contextmanager
def event_store_v2() -> Iterator[EventStoreV2]:
    """The v2 Postgres event store built from the same ``DATABASE_URL``."""
    engine = create_engine_from_env()
    try:
        yield PostgresEventStoreV2(engine, contract_schemas())
    finally:
        engine.dispose()


class UnusedEventStore:
    """An :class:`~harness.core.ports.EventStore` that must never be called.

    ``generate`` needs a :class:`~harness.core.pack.PackImporter` only for
    :meth:`~harness.core.pack.PackImporter.check`, which validates a pack
    without writing anything, so the command needs no database. Passing this
    instead of a real store keeps ``generate`` runnable with no ``DATABASE_URL``
    and turns any future write into a loud failure instead of a silent one.
    """

    def transaction(self) -> AbstractContextManager[EventTransaction]:
        raise AssertionError("generate must not use the event store")

    def read_session(
        self,
        session_id: str,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        raise AssertionError("generate must not use the event store")

    def read_all(
        self,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        raise AssertionError("generate must not use the event store")


def _conforms() -> None:  # pragma: no cover - evaluated by mypy only
    _store: EventStore = UnusedEventStore()
