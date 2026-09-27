"""Migration e5a7c9b1d3f4 (#175): an existing database's ws/session/thread
lists survive the upgrade that adds the per-user index views."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pack_artifact_types import PACK_ARTIFACT_TYPES
from sqlalchemy.engine import Engine

from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.adapters.postgres.migrate import locate_migrations_dir
from harness.api.v2.deps import event_store_v2_of, user_id_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import import_pack_v2

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from api_harness import build_app  # noqa: E402

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def _ids(client: TestClient, url: str, field: str) -> list[str]:
    """Every item's ``field`` across all pages of ``url`` at ``limit=2``."""
    ids, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get(f"/v2{url}", params=params).json()
        ids += [item[field] for item in body["items"]]
        cursor = body.get("next_cursor")
        if cursor is None:
            return ids


def test_upgrade_backfills_the_index_views(pg_url: str, pg_engine: Engine) -> None:
    app = build_app()
    app.state.pack_v2 = import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)
    store = PostgresEventStoreV2(pg_engine, ContractSchemas.load())
    app.dependency_overrides[event_store_v2_of] = lambda: store
    user = f"usr_{uuid.uuid4().hex}"  # the shared database is never emptied
    app.dependency_overrides[user_id_of] = lambda: user
    client = TestClient(app)

    def post(url: str, body: dict[str, object]) -> dict[str, object]:
        response = client.post(
            f"/v2{url}", json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
        )
        assert response.status_code == 201, response.text
        return response.json()

    sessions = [
        post("/sessions", {"pack_id": "software-engineering", "topic_id": "network"})["id"]
        for _ in range(3)
    ]
    ws = [post("/ws", {"session_id": sessions[0]})["ws_id"] for _ in range(3)]
    threads = [
        post(f"/ws/{ws[0]}/threads", {"target": {"kind": "artifact"}})["payload"]["thread_id"]  # type: ignore[index]
        for _ in range(3)
    ]

    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            # back to the pre-#175 shape: no index documents, thread keys by thread_id
            command.downgrade(config, "d7b1e3f5a9c2")
            assert _ids(client, "/sessions", "id") == []
            assert _ids(client, "/ws", "ws_id") == []
        finally:
            command.upgrade(config, "head")

    assert _ids(client, "/sessions", "id") == sessions
    assert _ids(client, "/ws", "ws_id") == ws[::-1]
    assert _ids(client, f"/ws/{ws[0]}/threads", "thread_id") == threads
