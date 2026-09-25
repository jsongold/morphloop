"""The loaded pack's topic tree over `/v2/topics` (#116)."""

from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pack_artifact_types import PACK_ARTIFACT_TYPES
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports import to_plain_object
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.openapi_v2 import V2_DIR, load_merged_openapi_v2_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


@pytest.fixture
def client() -> TestClient:
    app, _ = build_app()
    app.state.pack_v2 = import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
    app.state.event_store_v2 = InMemoryEventStoreV2(ContractSchemas.load())
    return TestClient(app)


def test_topics_match_pack_tree_and_session_accepts_every_returned_id(client: TestClient) -> None:
    response = client.get("/v2/topics")
    assert response.status_code == 200
    pack = client.app.state.pack_v2
    body = response.json()
    assert body == {
        "pack_id": pack.pack_id,
        "pack_hash": pack.pack_hash,
        "topics": [to_plain_object(topic) for topic in pack.topics],
    }

    def ids(topics: list[dict[str, Any]]) -> list[str]:
        return [
            topic_id
            for topic in topics
            for topic_id in [topic["id"], *ids(topic.get("topics", []))]
        ]

    for topic_id in ids(body["topics"]):
        created = client.post(
            "/v2/sessions",
            json={"pack_id": body["pack_id"], "topic_id": topic_id},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201
        assert created.json()["topic_id"] == topic_id


def test_real_response_matches_openapi_contract(client: TestClient) -> None:
    spec = load_merged_openapi_v2_spec()
    uri = (V2_DIR / "root.yaml").resolve().as_uri()
    registry = Registry().with_resource(uri, DRAFT202012.create_resource(spec))
    schema = {"$ref": f"{uri}#/paths/~1topics/get/responses/200/content/application~1json/schema"}
    response = client.get("/v2/topics")
    assert response.status_code == 200
    Draft202012Validator(schema, registry=registry).validate(response.json())


def test_topics_omit_fields_outside_the_learner_view(client: TestClient) -> None:
    pack = client.app.state.pack_v2
    topic = to_plain_object(pack.topics[0])
    topic["answer"] = "hidden"
    topic["topics"][0]["answer"] = "also hidden"
    client.app.state.pack_v2 = replace(pack, topics=(topic,))

    body = client.get("/v2/topics").json()
    assert "answer" not in body["topics"][0]
    assert "answer" not in body["topics"][0]["topics"][0]
