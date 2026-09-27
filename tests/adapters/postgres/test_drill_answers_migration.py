"""Migration ``b8d2f4a6c0e3``: old ``<ws>/<item>/<position>`` answer documents
page in creation order after the re-key (#178), without a manual rebuild."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from api_harness import build_app
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.adapters.postgres.migrate import locate_migrations_dir
from harness.api.v2.deps import event_store_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.testing.contracts import CONTRACTS_DIR

USER = "usr_local"  # MORPHLOOP_USER_ID default


def test_upgrade_rekeys_old_answer_documents(
    pg_url: str, pg_engine: Engine, tmp_path: Path
) -> None:
    ws = f"ws_{uuid.uuid4().hex}"
    # "b-item" answered first: the old key order (by item) is not creation order.
    answers = [("b-item", 7), ("a-item", 8), ("b-item", 9)]
    event_ids = [str(uuid.uuid4()) for _ in answers]
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            command.downgrade(config, "e2a4c6b8d0f1")
            with pg_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO view_documents_v2 (view, key, document)"
                        " VALUES ('ws', :ws, CAST(:doc AS jsonb))"
                    ),
                    {"ws": ws, "doc": json.dumps({"user_id": USER, "session_id": "ses_1"})},
                )
                for (item, position), event_id in zip(answers, event_ids, strict=True):
                    doc = {"event_id": event_id, "user_id": USER, "ws_id": ws, "item_id": item}
                    conn.execute(
                        text(
                            "INSERT INTO view_documents_v2 (view, key, document)"
                            " VALUES ('drill.answers', :key, CAST(:doc AS jsonb))"
                        ),
                        {"key": f"{ws}/{item}/{position:012d}", "doc": json.dumps(doc)},
                    )
        finally:
            command.upgrade(config, "head")

    store = PostgresEventStoreV2(pg_engine, ContractSchemas(CONTRACTS_DIR))
    app = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: store
    url = f"/v2/ws/{ws}/drills/answers"
    with TestClient(app) as client:
        first = client.get(url, params={"limit": 2}).json()
        rest = client.get(url, params={"cursor": first["next_cursor"]}).json()
    assert [a["answer_event_id"] for a in first["items"] + rest["items"]] == event_ids
    assert rest["next_cursor"] is None
    with pg_engine.connect() as conn:
        keys = conn.execute(
            text("SELECT key FROM view_documents_v2 WHERE view = 'drill.answers' AND key LIKE :p"),
            {"p": f"{ws}/%"},
        ).scalars()
        assert sorted(keys) == [f"{ws}/{p:012d}" for _, p in answers]
