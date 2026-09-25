"""A LabArtifactService over in-memory fakes, shared with ``tests/api/v2/test_artifact.py``."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from harness.core.artifact_lab import LabArtifactService
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import DomainAdapterRegistry
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.lab_runtime import ResourceLimits
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeTerminalBridge,
    FakeTerminalTool,
)
from harness.testing.fakes_v2 import InMemoryEventStoreV2, contract_schemas_with_probe

WS_ID = "ws_1"
SPEC_ID = "lab-1"
SPEC = {
    "id": SPEC_ID,
    "type": "lab",
    "labels": ["troubleshooting"],
    "spec": {
        "environment": {
            "id": "lab-1.env",
            "fixture": "fake.lab",
            "image": {"repository": "docker.io/library/alpine", "digest": "sha256:" + "a" * 64},
            "params": {"zone": "example"},
        },
        "allowed_fixtures": ["fake.lab"],
        "allowed_checks": ["fake.exit"],
        "idle_seconds": 60,
    },
}
SPECS = {SPEC_ID: SPEC}
USER_ID = "usr_1"


def _schemas_with_ws_created(directory: Path) -> ContractSchemas:
    """Contracts plus a stand-in ``ws.created`` (the ws resource owns the real one)."""
    contracts = contract_schemas_with_probe(directory).contracts_dir
    path = contracts / "schemas/events/payloads/ws.created/1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://morphloop.dev/contracts/schemas/events/payloads/ws.created/1.json",
                "x-envelope": 2,
                "x-actors": ["learner"],
                "type": "object",
            }
        ),
        encoding="utf-8",
    )
    return ContractSchemas(contracts)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass
class LabFixture:
    service: LabArtifactService
    store: InMemoryEventStoreV2
    labs: FakeLabRuntime
    terminals: FakeTerminalBridge
    clock: Clock


def build(tmp_path: Path) -> LabFixture:
    clock = Clock()
    store = InMemoryEventStoreV2(_schemas_with_ws_created(tmp_path), now=clock)
    with store.transaction() as tx:
        tx.append(
            EventV2(
                id=str(uuid.uuid4()),
                type="ws.created",
                actor="learner",
                user_id=USER_ID,
                session_id="ses_1",
                ws_id=WS_ID,
                payload={},
            )
        )
    adapters = DomainAdapterRegistry()
    adapters.register(
        FakeDomainAdapter(
            adapter_id="fake",
            version="0.1.0",
            fixtures={
                "lab": FakeFixtureProvider(
                    limits=ResourceLimits(
                        cpus=1, memory_bytes=1 << 28, pids=64, lifetime_seconds=600
                    ),
                    network="none",
                )
            },
            checks={"exit": FakeCommandExitCheck(timeout_seconds=5, max_output_bytes=4096)},
            tools={"terminal": FakeTerminalTool()},
        )
    )
    labs, terminals = FakeLabRuntime(), FakeTerminalBridge()
    service = LabArtifactService(
        store=store,
        labs=labs,
        terminals=terminals,
        adapters=adapters,
        now=clock,
    )
    return LabFixture(service, store, labs, terminals, clock)


def start(f: LabFixture) -> str:
    """Start the test lab; return its artifact id."""
    started = f.service.start(
        SPECS, user_id=USER_ID, ws_id=WS_ID, spec_id=SPEC_ID, event_id=str(uuid.uuid4())
    )
    return str(started["artifact_id"])


def new_key() -> str:
    return str(uuid.uuid4())
