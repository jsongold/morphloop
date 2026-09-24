"""`/v2` auto-include and DI (issue #52, #34).

Covers the three things the issue asks for:

- a resource adds routes just by dropping a module under
  `harness/api/v2/routes/` -- no central list is edited;
- the v0.1 API is unaffected by mounting `/v2`;
- `EventStoreV2Dep` / `EventTransactionV2Dep` can be swapped for an
  `InMemoryEventStoreV2` via `app.dependency_overrides`, same idiom as
  `harness.api.routes.backend_of`.

The temporary route module is written to a throwaway directory and picked up
by monkeypatching `harness.api.v2.routes.__path__` to include it -- the real
`harness/api/v2/routes/` package on disk is never touched. `build_v2_router()`
re-scans that path on every call (see `harness/api/v2/__init__.py`), so a
plain `build_app()` after the monkeypatch is enough to include it.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harness.api.v2.routes as v2_routes
from harness.api.v2.deps import event_store_v2_of
from harness.testing.fakes_v2 import InMemoryEventStoreV2, contract_schemas_with_probe

# api_harness owns the app-under-fakes builder; pytest only puts a test
# directory on sys.path once it collects from it, so importing it needs the
# path itself (same trick as tests/e2e/test_v01_chain.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_harness import build_app  # noqa: E402

_PROBE_MODULE = """
from __future__ import annotations

import uuid

from fastapi import APIRouter

from harness.api.v2.deps import EventStoreV2Dep, EventTransactionV2Dep
from harness.core.ports.events_v2 import EventV2
from harness.testing.fakes_v2 import PROBE_EVENT_TYPE

router = APIRouter()


@router.get("/autoinclude-probe/ping")
def ping_probe() -> dict[str, bool]:
    return {"ok": True}


@router.post("/autoinclude-probe/events")
def create_probe_event(tx: EventTransactionV2Dep) -> dict[str, str]:
    result = tx.append(
        EventV2(
            id=str(uuid.uuid4()),
            type=PROBE_EVENT_TYPE,
            actor="system",
            user_id="usr_probe",
            payload={"note": "autoincluded"},
        )
    )
    return {"id": result.event.id}


@router.get("/autoinclude-probe/events")
def list_probe_events(store: EventStoreV2Dep) -> dict[str, list[str]]:
    events = store.read(user_id="usr_probe")
    return {"notes": [str(e.payload["note"]) for e in events]}
"""


@pytest.fixture
def temporary_route_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drop a resource route module in a throwaway directory on `routes.__path__`."""
    extra_dir = tmp_path / "extra_v2_routes"
    extra_dir.mkdir()
    module_name = f"probe_{uuid.uuid4().hex}"
    (extra_dir / f"{module_name}.py").write_text(_PROBE_MODULE, encoding="utf-8")

    monkeypatch.setattr(v2_routes, "__path__", [*v2_routes.__path__, str(extra_dir)])
    yield
    sys.modules.pop(f"{v2_routes.__name__}.{module_name}", None)


def test_temporary_route_module_is_auto_included(temporary_route_module: None) -> None:
    app, _fixture = build_app()

    with TestClient(app) as client:
        response = client.get("/v2/autoinclude-probe/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_v1_endpoints_are_unchanged(temporary_route_module: None) -> None:
    app, _fixture = build_app()

    with TestClient(app) as client:
        health = client.get("/health")
        packs = client.get("/packs")

    assert health.status_code == 200
    assert packs.status_code == 200


def test_dependency_override_uses_in_memory_store(
    temporary_route_module: None, tmp_path: Path
) -> None:
    schemas = contract_schemas_with_probe(tmp_path / "contracts_copy")
    in_memory_store = InMemoryEventStoreV2(schemas)

    app, _fixture = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: in_memory_store
    try:
        with TestClient(app) as client:
            created = client.post("/v2/autoinclude-probe/events")
            listed = client.get("/v2/autoinclude-probe/events")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert listed.status_code == 200
    assert listed.json() == {"notes": ["autoincluded"]}
