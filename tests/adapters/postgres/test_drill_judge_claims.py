"""Drill judge single-flight across two API workers on real Postgres (#181).

Two apps (two workers) share one event log and one Postgres ``claims`` table,
each through its own engine. The same answer is posted to both at once: the
LLM runs once, the other worker answers ``pending``, and one ``drill.judged``
is recorded.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api_harness import build_app
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES
from sqlalchemy import create_engine, text

from harness.adapters.postgres.claims import PostgresClaimStore
from harness.api.v2.deps import event_store_v2_of
from harness.api.v2.routes.drill import drill_judge_config_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.llm import LLMProvenance, LLMRequest, LLMResponse
from harness.testing.fakes_v2 import InMemoryEventStoreV2, seed_ws
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).resolve().parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"
URL = "/v2/ws/ws_1/drills/dns-resolver-text/answers"
LLM = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="judge",
    prompt_version="1",
    generation_parameters={},
)


class BlockingLLM:
    """Holds the winner at the LLM until the other worker has answered."""

    def __init__(self) -> None:
        self.calls = 0
        self.loser_answered = threading.Event()

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        assert self.loser_answered.wait(10), "the other worker never answered"
        return LLMResponse(output={"missing": []}, provenance=LLM)


@pytest.fixture
def workers(pg_url: str) -> Iterator[tuple[list[TestClient], InMemoryEventStoreV2, BlockingLLM]]:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    seed_ws(store, "ws_1", session_id="ses_1")
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    llm = BlockingLLM()
    engines = [create_engine(pg_url) for _ in range(2)]
    clients = []
    for engine in engines:
        app = build_app()
        app.state.pack_v2 = pack
        app.state.claims = PostgresClaimStore(engine)
        app.state.generated_documents = InMemoryGeneratedDocumentStore()
        app.state.drill_llm = llm
        app.dependency_overrides[event_store_v2_of] = lambda: store
        app.dependency_overrides[drill_judge_config_of] = lambda: (LLM, "Judge the gap.")
        clients.append(TestClient(app))
    try:
        yield clients, store, llm
    finally:
        for engine in engines:
            engine.dispose()


def test_concurrent_judge_on_two_workers_calls_the_llm_once(
    workers: tuple[list[TestClient], InMemoryEventStoreV2, BlockingLLM], pg_url: str
) -> None:
    clients, store, llm = workers
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    statuses: list[str] = []

    def post(client: TestClient) -> None:
        barrier.wait()
        body: Any = client.post(URL, json={"actual": "?"}, headers={"Idempotency-Key": key})
        assert body.status_code == 201
        statuses.append(body.json()["judgment_status"])
        if statuses[-1] == "pending":
            llm.loser_answered.set()

    threads = [threading.Thread(target=post, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(statuses) == ["complete", "pending"]
    assert llm.calls == 1
    assert [e.type for e in store.read(ws_id="ws_1")].count("drill.judged") == 1
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            held = conn.execute(
                text("SELECT count(*) FROM claims WHERE key = :k"), {"k": f"judge:{key}"}
            ).scalar_one()
    finally:
        engine.dispose()
    assert held == 0, "the lease is released after the judgment is recorded"
