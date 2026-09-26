"""Migration ``e2a4c6b8d0f1``: old one-document chat threads survive the
per-message layout (#177) without a manual rebuild."""

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
from sqlalchemy.engine import Connection, Engine

from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.adapters.postgres.migrate import locate_migrations_dir
from harness.api.v2.deps import event_store_v2_of
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.json_types import JsonObject
from harness.testing.contracts import CONTRACTS_DIR

USER = "usr_local"  # MORPHLOOP_USER_ID default


def _insert_event(conn: Connection, type_: str, actor: str, ws: str, payload: JsonObject) -> None:
    conn.execute(
        text(
            "INSERT INTO events_v2 (id, type, actor, user_id, ws_id, payload)"
            " VALUES (:id, :type, :actor, :user, :ws, CAST(:payload AS jsonb))"
        ),
        {
            "id": str(uuid.uuid4()),
            "type": type_,
            "actor": actor,
            "user": USER,
            "ws": ws,
            "payload": json.dumps(payload),
        },
    )


def _put_view(conn: Connection, view: str, key: str, doc: JsonObject) -> None:
    conn.execute(
        text(
            "INSERT INTO view_documents_v2 (view, key, document)"
            " VALUES (:view, :key, CAST(:doc AS jsonb))"
        ),
        {"view": view, "key": key, "doc": json.dumps(doc)},
    )


def test_upgrade_rekeys_old_thread_document(pg_url: str, pg_engine: Engine, tmp_path: Path) -> None:
    ws, thread = f"ws_{uuid.uuid4().hex}", f"thr_{uuid.uuid4().hex}"
    messages = [
        {
            "message_id": f"msg_{i}",
            "role": role,
            "text": f"t{i}",
            "created_at": "2026-09-26T00:00:00Z",
        }
        for i, role in enumerate(["learner", "assistant", "learner"])
    ]
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", pg_url)
        try:
            command.downgrade(config, "d7b1e3f5a9c2")
            with pg_engine.begin() as conn:
                for m in messages:
                    type_ = "chat.sent" if m["role"] == "learner" else "chat.replied"
                    payload = {"thread_id": thread, "message_id": m["message_id"]}
                    _insert_event(conn, type_, str(m["role"]), ws, payload)
                _put_view(conn, "chat.messages", f"{ws}/{thread}", {"messages": messages})
                _put_view(
                    conn,
                    "chat.thread",
                    thread,
                    {
                        "ws_id": ws,
                        "user_id": USER,
                        "session_id": None,
                        "target": None,
                        "labels": [],
                    },
                )
        finally:
            command.upgrade(config, "head")

    store = PostgresEventStoreV2(pg_engine, ContractSchemas(CONTRACTS_DIR))
    app = build_app()
    app.dependency_overrides[event_store_v2_of] = lambda: store
    url = f"/v2/ws/{ws}/threads/{thread}/messages"
    with TestClient(app) as client:
        first = client.get(url, params={"limit": 2}).json()
        rest = client.get(url, params={"cursor": first["next_cursor"]}).json()
    assert first["messages"] + rest["messages"] == messages
    assert rest["next_cursor"] is None
    with pg_engine.connect() as conn:
        old = conn.execute(
            text(
                "SELECT count(*) FROM view_documents_v2 WHERE view = 'chat.messages' AND key = :k"
            ),
            {"k": f"{ws}/{thread}"},
        ).scalar_one()
    assert old == 0
