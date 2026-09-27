"""`create_app(extensions=...)` (#95): an app's routers mount under `/v2` and its
artifact types are what `PackV2Dep` imports the pack with."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from api_harness import build_app
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.api.periodic import CLAIMS_PURGE, PeriodicJob
from harness.api.v2.deps import PackV2Dep
from harness.sdk import AppExtension, Artifact, PackV2ImportError
from harness.testing.claims import InMemoryClaimStore

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
    app = build_app(extensions=[extension])
    assert app.state.artifact_types == (Probe,)
    with TestClient(app) as client:
        assert client.get("/v2/probe/ping").json() == {"ok": True}
        assert client.get("/probe/ping").status_code == 404


def test_app_without_artifact_types_refuses_a_pack_with_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    app = build_app(extensions=[AppExtension(routers=(router,))])
    assert app.state.artifact_types == ()
    with (
        TestClient(app) as client,
        pytest.raises(PackV2ImportError, match="'lab' is not registered \\(none\\)"),
    ):
        client.get("/v2/probe/pack")


def test_pack_is_imported_with_the_extension_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPHLOOP_PACK_V2_DIR", str(PACK_DIR))
    extension = AppExtension(routers=(router,), artifact_types=PACK_ARTIFACT_TYPES)
    app = build_app(extensions=[extension])
    with TestClient(app) as client:
        assert client.get("/v2/probe/pack").json() == {"pack_id": "software-engineering"}


def test_periodic_jobs_start_and_stop_with_the_app_and_replace_the_sdk_job_by_name() -> None:
    ran = threading.Event()
    tick = PeriodicJob("tick", timedelta(milliseconds=50), ran.set)
    purge = PeriodicJob(CLAIMS_PURGE, timedelta(days=1), lambda: None)
    app = build_app(extensions=[AppExtension(periodic_jobs=(tick, purge))])
    app.state.claims = InMemoryClaimStore()
    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler.running
        assert sorted(j.id for j in scheduler.get_jobs()) == [CLAIMS_PURGE, "tick"]
        assert scheduler.get_job(CLAIMS_PURGE).trigger.interval == timedelta(days=1)
        assert ran.wait(timeout=5)
    assert not scheduler.running


def test_bare_app_schedules_the_sdk_claims_purge() -> None:
    app = build_app()
    with TestClient(app):
        assert [j.id for j in app.state.scheduler.get_jobs()] == [CLAIMS_PURGE]
