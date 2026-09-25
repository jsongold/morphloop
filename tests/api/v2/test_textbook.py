"""`/v2/textbook` over the fixture v2 pack, checked against contracts/openapi/v0.2 (#55)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.pack.v2 import import_pack_v2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore
from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)
PATHS = load_merged_openapi_v2_spec()["paths"]


def _check(body: Any, url: str) -> None:
    schema = PATHS[url]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator(schema).validate(body)


@pytest.fixture(scope="module")
def client() -> Any:
    app, _ = build_app()
    app.state.pack_v2 = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    with TestClient(app) as c:
        yield c


def test_reading_list(client: TestClient) -> None:
    res = client.get("/v2/textbook/docs", params={"topic_id": "network.dns.resolution"})
    assert res.status_code == 200
    _check(res.json(), "/textbook/docs")
    assert [d["id"] for d in res.json()["docs"]] == ["dns-resolution"]


def test_doc(client: TestClient) -> None:
    res = client.get("/v2/textbook/docs/dns-resolution")
    assert res.status_code == 200
    _check(res.json(), "/textbook/docs/{doc_id}")
    assert "origin:pack" in res.json()["labels"]


@pytest.mark.parametrize(
    "url", ["/v2/textbook/docs?topic_id=nope", "/v2/textbook/docs/nope", "/v2/textbook/docs"]
)
def test_errors_are_problems(client: TestClient, url: str) -> None:
    res = client.get(url)
    assert res.status_code in (400, 404, 422)
    assert res.headers["content-type"].startswith("application/problem+json")


def test_empty_topic_id_is_rejected(client: TestClient) -> None:
    res = client.get("/v2/textbook/docs", params={"topic_id": ""})
    assert res.status_code in (400, 422)


def test_topic_id_rejects_nul(client: TestClient) -> None:
    # #93 hardening: a query param carrying a free-text value uses Text too.
    res = client.get("/v2/textbook/docs", params={"topic_id": "a\u0000b"})
    assert res.status_code in (400, 422)
    assert res.headers["content-type"].startswith("application/problem+json")
