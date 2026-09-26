"""Composition root: the real objects the HTTP/WebSocket layer runs against.

Same idiom as ``harness/cli/wiring.py`` -- core owns the Ports, ``harness/adapters/*``
(and an app's domain adapters) implement them, and this is where the API picks which
implementation is used: the Postgres event store from ``DATABASE_URL``, the
litellm provider (the pack's model string selects the provider and litellm reads
the matching API key from the environment), the Docker lab runtime and its PTY
bridge (domain adapters belong to apps). The pack source is
not wired here: packs are imported by the CLI (ADR-0015) and the loop reads only
the DB projection through :class:`~harness.core.pack.PackCatalog`.

``create_app`` builds this once at startup. If it cannot be built (no database
URL, no Docker daemon) the failure is logged and ``/health`` still answers, so
the stack reports *why* it is down instead of refusing to start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from harness.adapters.docker_lab import DockerLabRuntime
from harness.adapters.postgres.engine import create_engine_from_env
from harness.adapters.postgres.event_store import PostgresEventStore
from harness.adapters.pty import DockerTerminalBridge
from harness.cli import wiring
from harness.core.contract_schemas import ContractSchemas
from harness.core.loop import LearningLoop
from harness.core.ports import EventStore
from harness.core.settings import Settings

HARNESS_VERSION_ENV_VAR = "HARNESS_VERSION"

logger = logging.getLogger(__name__)


def harness_version() -> str:
    """The version recorded as provenance on ``session.started`` (ADR-0010)."""
    override = Settings().harness_version
    if override:
        return override
    try:
        return version("morphloop")
    except PackageNotFoundError:  # pragma: no cover - an installed harness has metadata
        return "0.0.0"


@dataclass(frozen=True, slots=True)
class Backend:
    """The wired loop plus the store the API reads idempotency keys from."""

    loop: LearningLoop
    store: EventStore


def build_backend() -> Backend:
    """Wire the real adapters. Raises when one of them cannot be reached."""
    import docker  # noqa: PLC0415 - only a real backend needs the daemon

    store = PostgresEventStore(create_engine_from_env())
    client = docker.from_env()
    labs = DockerLabRuntime(client)
    reaped = labs.reap_expired()
    if reaped:
        logger.info("reaped %d expired lab container(s) at startup", len(reaped))
    loop = LearningLoop(
        store=store,
        schemas=ContractSchemas.load(),
        adapters=wiring.domain_adapters(),
        algorithms=wiring.algorithms(),
        llm=wiring.llm_provider(),
        labs=labs,
        terminals=DockerTerminalBridge(),
        harness_version=harness_version(),
    )
    return Backend(loop=loop, store=store)
