"""API tests for the highlight resource (#34, #60): create/list/remove over `/v2`."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from harness.api.problems import install_handlers
from harness.api.v2 import build_v2_router
from harness.api.v2.deps import event_store_v2_of, pack_v2_of
from harness.core.pack.v2.importer import import_pack_v2
from harness.testing.fakes_v2 import InMemoryEventStoreV2, contract_schemas_with_probe
from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

PATHS = load_merged_openapi_v2_spec()["paths"]


def _retrieve_component_file(uri: str) -> Resource[Any]:
    # `load_merged_openapi_v2_spec` rewrites relative $refs (e.g.
    # `../components/highlight.yaml#/Highlight`) into absolute `file://` URIs;
    # resolve those on demand from disk instead of duplicating the schemas.
    path = Path(uri.removeprefix("file://"))
    return DRAFT202012.create_resource(yaml.safe_load(path.read_text(encoding="utf-8")))


_REGISTRY: Registry[Any] = Registry(retrieve=_retrieve_component_file)


def _check(body: Any, url: str, method: str, status: str) -> None:
    schema = PATHS[url][method]["responses"][status]["content"]["application/json"]["schema"]
    Draft202012Validator(schema, registry=_REGISTRY).validate(body)


PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"

# api_harness owns nothing v2-specific; the app under test here is a bare
# FastAPI with only the v2 router and its dependency overrides, same idiom as
# tests/api/v2/test_router_autoinclude.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WS_ID = "ws_01"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = FastAPI()
    install_handlers(app)
    app.include_router(build_v2_router())
    schemas = contract_schemas_with_probe(tmp_path / "contracts_copy")
    store = InMemoryEventStoreV2(schemas)
    pack = import_pack_v2(PACK_DIR, schemas=schemas)
    app.dependency_overrides[event_store_v2_of] = lambda: store
    app.dependency_overrides[pack_v2_of] = lambda: pack
    with TestClient(app) as test_client:
        yield test_client


def _anchor(start: int = 0, end: int = 5) -> dict[str, Any]:
    return {
        "doc_id": "dns-resolution",
        "block_id": "intro",
        "selector": [
            {"type": "TextQuoteSelector", "exact": "hello"},
            {"type": "TextPositionSelector", "start": start, "end": end},
        ],
    }


def test_create_list_remove_flow(client: TestClient) -> None:
    created = client.post(
        f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor(), "labels": ["concept"]}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    _check(body, "/ws/{ws_id}/highlights", "post", "201")
    assert body["ws_id"] == WS_ID
    assert body["anchor"] == _anchor()
    assert body["labels"] == ["concept"]
    assert "removed" not in body
    highlight_id = body["highlight_id"]
    assert highlight_id.startswith("hl_")

    listed = client.get(f"/v2/ws/{WS_ID}/highlights")
    assert listed.status_code == 200
    _check(listed.json(), "/ws/{ws_id}/highlights", "get", "200")
    assert listed.json() == {"highlights": [body]}

    removed = client.delete(f"/v2/ws/{WS_ID}/highlights/{highlight_id}")
    assert removed.status_code == 204

    listed_after = client.get(f"/v2/ws/{WS_ID}/highlights")
    assert listed_after.json() == {"highlights": []}

    removed_again = client.delete(f"/v2/ws/{WS_ID}/highlights/{highlight_id}")
    assert removed_again.status_code == 404
    assert removed_again.json()["code"] == "not-found"


def test_create_defaults_labels_to_empty(client: TestClient) -> None:
    created = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor()})
    assert created.status_code == 201, created.text
    assert created.json()["labels"] == []


def test_create_rejects_a_label_outside_the_pack_vocabulary(client: TestClient) -> None:
    response = client.post(
        f"/v2/ws/{WS_ID}/highlights",
        json={"anchor": _anchor(), "labels": ["not-a-real-label"]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation-failed"


def test_create_rejects_start_not_before_end(client: TestClient) -> None:
    response = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor(start=5, end=5)})
    assert response.status_code == 422
    assert response.json()["code"] == "validation-failed"


def test_create_rejects_a_second_block_in_the_selector(client: TestClient) -> None:
    anchor = _anchor()
    anchor["selector"] = [anchor["selector"][0], anchor["selector"][1], anchor["selector"][1]]
    response = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": anchor})
    assert response.status_code == 422


def test_create_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor(), "extra": True})
    assert response.status_code == 422


def test_remove_unknown_highlight_is_404(client: TestClient) -> None:
    response = client.delete(f"/v2/ws/{WS_ID}/highlights/hl_missing")
    assert response.status_code == 404
    assert response.json()["code"] == "not-found"


def test_malformed_ws_id_is_400(client: TestClient) -> None:
    response = client.get("/v2/ws/not-a-ws-id/highlights")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid-request"


def test_highlights_are_scoped_to_their_ws(client: TestClient) -> None:
    client.post("/v2/ws/ws_a/highlights", json={"anchor": _anchor()})
    other_ws = client.get("/v2/ws/ws_b/highlights")
    assert other_ws.json() == {"highlights": []}


def test_create_resend_with_same_key_replays(client: TestClient) -> None:
    headers = {"Idempotency-Key": "0190f5a2-7c3e-7d4b-8a1f-0000000000aa"}
    first = client.post(
        f"/v2/ws/{WS_ID}/highlights",
        json={"anchor": _anchor(), "labels": ["concept"]},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/v2/ws/{WS_ID}/highlights",
        json={"anchor": _anchor(), "labels": ["concept"]},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json() == first.json()


def test_create_resend_with_same_key_different_body_is_conflict(client: TestClient) -> None:
    headers = {"Idempotency-Key": "0190f5a2-7c3e-7d4b-8a1f-0000000000ab"}
    first = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor()}, headers=headers)
    assert first.status_code == 201, first.text
    second = client.post(
        f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor(start=1, end=6)}, headers=headers
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "idempotency-key-reused"


def test_remove_resend_with_same_key_replays_after_tombstone(client: TestClient) -> None:
    created = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": _anchor()})
    highlight_id = created.json()["highlight_id"]
    headers = {"Idempotency-Key": "0190f5a2-7c3e-7d4b-8a1f-0000000000ac"}
    first = client.delete(f"/v2/ws/{WS_ID}/highlights/{highlight_id}", headers=headers)
    assert first.status_code == 204
    second = client.delete(f"/v2/ws/{WS_ID}/highlights/{highlight_id}", headers=headers)
    assert second.status_code == 204


@pytest.mark.parametrize("bad_start", ["0", True])
def test_create_rejects_non_strict_int_offsets(client: TestClient, bad_start: Any) -> None:
    anchor = _anchor()
    anchor["selector"][1]["start"] = bad_start
    response = client.post(f"/v2/ws/{WS_ID}/highlights", json={"anchor": anchor})
    assert response.status_code == 422, response.text
