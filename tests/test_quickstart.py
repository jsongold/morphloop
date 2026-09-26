"""The `examples/quickstart/` app actually serves `/v2` (#150).

Imports the example's `app.py` by file path (it is not a workspace package)
and swaps in the in-memory `/v2` fakes so the check needs no database -- the
same idiom `tests/api/v2/test_topics.py` and `tests/api/v2/test_drill.py` use
for the real app. This is what CI runs to prove the README's Quick start
commands describe a server that actually answers.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.core.contract_schemas import ContractSchemas
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

APP_PATH = Path(__file__).parents[1] / "examples" / "quickstart" / "app.py"


def _client() -> TestClient:
    spec = importlib.util.spec_from_file_location("quickstart_app", APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app: FastAPI = module.app
    app.state.event_store_v2 = InMemoryEventStoreV2(ContractSchemas.load())
    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    return TestClient(app)


def test_topics_sessions_and_drills_answer() -> None:
    client = _client()

    topics = client.get("/v2/topics")
    assert topics.status_code == 200
    assert topics.json()["pack_id"] == "quickstart"

    created = client.post(
        "/v2/sessions",
        json={"pack_id": "quickstart", "topic_id": "basics"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201
    assert created.json()["topic_id"] == "basics"

    drills = client.get("/v2/drills")
    assert drills.status_code == 200
    assert [item["id"] for item in drills.json()["items"]] == ["choice"]
