"""The loaded pack's artifact spec is served through its registered type."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.app import AppExtension
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.json_types import to_plain_object
from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

PACK = Path(__file__).resolve().parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def test_artifact_spec_uses_loaded_pack_and_matches_contract() -> None:
    app, _ = build_app(extensions=[AppExtension(artifact_types=PACK_ARTIFACT_TYPES)])
    app.state.pack_v2 = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    with TestClient(app) as client:
        response = client.get("/v2/artifact-specs/dns-resolution-flow")
        missing = client.get("/v2/artifact-specs/unknown")

    assert response.status_code == 200
    body = response.json()
    schema = load_merged_openapi_v2_spec()["paths"]["/artifact-specs/{spec_id}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    Draft202012Validator(schema).validate(body)
    pack_spec = app.state.pack_v2.documents["artifacts"]["artifacts/dns-resolution-flow.json"]
    assert body == {
        "id": "dns-resolution-flow",
        "type": "diagram",
        "labels": ["topic:network.dns.resolution"],
        "spec": to_plain_object(pack_spec["spec"]),
    }
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
