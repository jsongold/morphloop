"""`create_app(extensions=...)` (#95): an app's routers mount under `/v2` and its
artifact types are what `PackV2Dep` imports the pack with."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from api_harness import build_app
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.v2.deps import PackV2Dep
from harness.sdk import AppExtension, Artifact, PackV2ImportError

PACK_DIR = Path(__file__).parents[1] / "contracts/fixtures/pack-v2/valid/dns-pack"


class Probe(Artifact):
    type: ClassVar[str] = "test_probe"


router = APIRouter(prefix="/probe")


@router.get("/ping")
def ping() -> dict[str, bool]:
    return {"ok": True}


@router.get("/pack")
def pack_id(pack: PackV2Dep) -> dict[str, str]:
    return {"pack_id": pack.pack_id}


def test_extension_routers_mount_under_v2_and_types_land_on_state() -> None:
    extension = AppExtension(routers=(router,), artifact_types=(Probe,))
    app, _ = build_app(extensions=[extension])
    assert app.state.artifact_types == (Probe,)
    with TestClient(app) as client:
        assert client.get("/v2/probe/ping").json() == {"ok": True}
        assert client.get("/probe/ping").status_code == 404


def test_app_without_artifact_types_refuses_a_pack_with_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    app, _ = build_app(extensions=[AppExtension(routers=(router,))])
    assert app.state.artifact_types == ()
    with (
        TestClient(app) as client,
        pytest.raises(PackV2ImportError, match="'lab' is not registered \\(none\\)"),
    ):
        client.get("/v2/probe/pack")


def test_pack_is_imported_with_the_extension_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    extension = AppExtension(routers=(router,), artifact_types=PACK_ARTIFACT_TYPES)
    app, _ = build_app(extensions=[extension])
    with TestClient(app) as client:
        assert client.get("/v2/probe/pack").json() == {"pack_id": "software-engineering"}
