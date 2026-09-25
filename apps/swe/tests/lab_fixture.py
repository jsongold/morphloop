"""A LabArtifactService over the SDK's in-memory fakes, shared by the lab tests.

The store is a :class:`ConnectionTrackingStore`, so any test that reads or opens
a second transaction while one is open fails (#103).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from harness.sdk import ContractSchemas, DomainAdapterRegistry, EventTransactionV2, ResourceLimits
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeTerminalBridge,
    FakeTerminalTool,
)
from harness.testing.fakes_v2 import ConnectionTrackingStore, InMemoryEventStoreV2, seed_ws
from swe.artifacts.lab import LabArtifactService

WS_ID = "ws_1"
SESSION_ID = "ses_1"
SPEC_ID = "lab-1"
SPEC: dict[str, Any] = {
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
SPECS: dict[str, Any] = {SPEC_ID: SPEC}
USER_ID = "usr_1"


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
    store: ConnectionTrackingStore
    labs: FakeLabRuntime
    terminals: FakeTerminalBridge
    clock: Clock

    @contextmanager
    def tx(self) -> Iterator[EventTransactionV2]:
        """A request-like transaction for one service call."""
        with self.store.transaction() as tx:
            yield tx

    def types(self) -> list[str]:
        return [e.type for e in self.store.read(ws_id=WS_ID)]


def build() -> LabFixture:
    clock = Clock()
    store = ConnectionTrackingStore(InMemoryEventStoreV2(ContractSchemas.load(), now=clock))
    seed_ws(store, WS_ID, user_id=USER_ID, session_id=SESSION_ID)
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
    with f.tx() as tx:
        started = f.service.start(
            tx,
            SPECS,
            user_id=USER_ID,
            session_id=SESSION_ID,
            ws_id=WS_ID,
            spec_id=SPEC_ID,
            event_id=new_key(),
        )
    return str(started["artifact_id"])


def new_key() -> str:
    return str(uuid.uuid4())
