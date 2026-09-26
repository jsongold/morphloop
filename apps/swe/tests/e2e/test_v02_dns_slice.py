"""The v0.2.0 DNS slice, API-level, over the real SWE app (issue #68, parent #34).

Unlike ``tests/e2e/test_v01_chain.py`` (a real Docker lab against a real
``docker compose`` stack), this drives ``swe.app.create_swe_app`` in process
with ``TestClient``, a real Postgres ``EventStoreV2`` (the same
container-or-``TEST_DATABASE_URL`` fixture ``tests/adapters/postgres/conftest.py``
uses, self-contained here to keep the app/SDK test trees independent) and a
fake LLM (:class:`~harness.adapters.fake_llm.FakeDevLLMProvider`, #130). The lab
artifact runs over the SDK's in-memory lab fakes (:mod:`harness.testing.fakes`,
the same doubles ``apps/swe/tests/test_lab_routes.py`` uses) under a fake ``dns``
domain adapter, so nothing here needs the Docker daemon or a lab image.

Flow (issue #34, #68; the schedule step is deferred to #67 per the issue's
latest comment -- it is not built yet): topics -> session -> ws -> read the
textbook -> highlight -> thread -> chat (fake reply) -> memo -> start the lab
artifact -> check it -> answer the artifact drill -> judged gap. Every event is
asserted in position order, and ``harness.cli.rebuild.rebuild`` (the same
function the CLI's ``rebuild`` command calls) is asserted to reproduce the same
view documents.

Skipped exactly like ``tests/adapters/postgres/conftest.py``'s own tests: no
Docker daemon and no ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from harness.adapters.fake_llm import FAKE_REPLY_TEXT, FakeDevLLMProvider
from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.api.v2.deps import user_id_of
from harness.cli.rebuild import rebuild as rebuild_v2_views
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import Check, CheckObservation
from harness.sdk import DomainAdapterRegistry, JsonObject, LabRuntime, ResourceLimits
from harness.testing.fakes import (
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeTerminalBridge,
    InMemoryEventStore,
)
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore
from swe.app import create_swe_app
from swe.artifacts.lab.service import LabArtifactService

REPO_ROOT = Path(__file__).resolve().parents[4]
_PG_IMAGE = "postgres:16"
_PG_USER = _PG_PASSWORD = _PG_DB = "morphloop_test"
_PG_READY_TIMEOUT_SECONDS = 60

PACK_ID = "software-engineering"
TOPIC_ID = "network.dns.resolver"
DOC_ID = "network.dns.resolver"
LAB_SPEC_ID = "diagnose-dns-resolver-misconfiguration-lab"
DRILL_ITEM_ID = "gen-diagnose-dns-resolver-misconfiguration-001"


# --- Postgres: same container-or-TEST_DATABASE_URL fixture as
# tests/adapters/postgres/conftest.py, kept local so apps/swe/tests does not
# import the SDK's own test tree (it is not part of harness.sdk / harness.testing).


def _start_pg_container() -> tuple[Any, str]:
    try:
        import docker
        from docker.errors import DockerException
    except ImportError:  # pragma: no cover - docker is a project dependency
        pytest.skip("docker SDK not installed")
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        pytest.skip(f"Docker is unavailable: {exc}")
    container = client.containers.run(  # type: ignore[call-overload]
        _PG_IMAGE,
        detach=True,
        remove=True,
        environment={
            "POSTGRES_USER": _PG_USER,
            "POSTGRES_PASSWORD": _PG_PASSWORD,
            "POSTGRES_DB": _PG_DB,
        },
        ports={"5432/tcp": ("127.0.0.1", None)},
    )
    try:
        deadline = time.monotonic() + _PG_READY_TIMEOUT_SECONDS
        port = None
        while port is None:
            container.reload()
            bindings = container.attrs["NetworkSettings"]["Ports"].get("5432/tcp")
            if bindings:
                port = bindings[0]["HostPort"]
            elif time.monotonic() > deadline:
                raise RuntimeError("postgres container published no port")
            else:
                time.sleep(0.1)
        url = f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}@127.0.0.1:{port}/{_PG_DB}"
        _wait_pg_ready(url, deadline)
    except BaseException:
        container.stop(timeout=1)
        raise
    return container, url


def _wait_pg_ready(url: str, deadline: float) -> None:
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    finally:
        engine.dispose()


def _migrate(url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)
        command.upgrade(config, "head")


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    container = None
    if not url:
        container, url = _start_pg_container()
    try:
        _migrate(url)
        yield url
    finally:
        if container is not None:
            container.stop(timeout=5)


@pytest.fixture
def pg_engine(pg_url: str) -> Iterator[Engine]:
    engine = create_engine(pg_url)
    try:
        yield engine
    finally:
        engine.dispose()


# --- a fake `dns` domain adapter: deterministic checks, no Docker, matching
# the real pack's fixture/check ids (`domains/dns/adapter.py`) so the real
# ``apps/swe/pack`` artifact spec and drill item can be used unchanged.


class _AlwaysPassCheck:
    """Ignores ``params``; the lab always "passes" (no real environment exists)."""

    def validate_params(self, params: JsonObject) -> None:
        return None

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        return CheckObservation(passed=True, observed={})


def _fake_dns_adapter() -> FakeDomainAdapter:
    checks: dict[str, Check] = {
        "resolver_answers": _AlwaysPassCheck(),
        "name_resolves": _AlwaysPassCheck(),
        "http_status": _AlwaysPassCheck(),
    }
    return FakeDomainAdapter(
        adapter_id="dns",
        version="0.1.0",
        fixtures={
            "resolver_lab": FakeFixtureProvider(
                limits=ResourceLimits(cpus=1, memory_bytes=1 << 28, pids=64, lifetime_seconds=600),
                network="none",
            )
        },
        checks=checks,
    )


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_dns_slice_v02(pg_engine: Engine) -> None:
    user_id = f"usr_e2e{uuid.uuid4().hex[:16]}"
    store = PostgresEventStoreV2(pg_engine, ContractSchemas.load())

    adapters = DomainAdapterRegistry()
    adapters.register(_fake_dns_adapter())
    lab_service = LabArtifactService(
        store=store,
        labs=FakeLabRuntime(),
        terminals=FakeTerminalBridge(),
        adapters=adapters,
    )

    # The real app, the real SWE pack (create_swe_app's own default); the client
    # never enters the lifespan, so the v0.1 backend is never wired either
    # (apps/swe/tests/test_lab_routes.py's own idiom).
    app = create_swe_app()
    app.dependency_overrides[user_id_of] = lambda: user_id
    app.state.event_store_v2 = store
    app.state.generated_documents = InMemoryGeneratedDocumentStore()
    app.state.artifact_lab = lab_service
    app.state.chat_llm = FakeDevLLMProvider()
    app.state.drill_llm = FakeDevLLMProvider()
    client = TestClient(app)
    pack: Any = app.state.pack_v2

    # --- 1. topics -> session -----------------------------------------------
    topics = client.get("/v2/topics")
    assert topics.status_code == 200
    assert topics.json()["pack_id"] == PACK_ID

    session = client.post(
        "/v2/sessions", json={"pack_id": PACK_ID, "topic_id": TOPIC_ID}, headers=_key()
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]

    # --- 2. POST /v2/ws ------------------------------------------------------
    ws = client.post("/v2/ws", json={"session_id": session_id}, headers=_key())
    assert ws.status_code == 201, ws.text
    ws_id = ws.json()["ws_id"]

    # --- 3. read the textbook doc --------------------------------------------
    reading_list = client.get("/v2/textbook/docs", params={"topic_id": TOPIC_ID})
    assert reading_list.status_code == 200
    assert [d["id"] for d in reading_list.json()["docs"]] == [DOC_ID]

    doc = client.get(f"/v2/textbook/docs/{DOC_ID}")
    assert doc.status_code == 200
    block: Any = next(b for b in doc.json()["blocks"] if b["id"] == "summary")
    quote = "stub resolver"
    start = block["body"].index(quote)
    end = start + len(quote)

    # --- 4. highlight -> thread -> chat (fake reply) -------------------------
    highlighted = client.post(
        f"/v2/ws/{ws_id}/highlights",
        json={
            "anchor": {
                "doc_id": DOC_ID,
                "block_id": "summary",
                "selector": [
                    {"type": "TextQuoteSelector", "exact": quote},
                    {"type": "TextPositionSelector", "start": start, "end": end},
                ],
            }
        },
        headers=_key(),
    )
    assert highlighted.status_code == 201, highlighted.text
    highlight_id = highlighted.json()["highlight_id"]

    thread = client.post(
        f"/v2/ws/{ws_id}/threads",
        json={
            "target": {
                "kind": "textbook_block",
                "doc_id": DOC_ID,
                "block_id": "summary",
                "highlight_id": highlight_id,
            }
        },
        headers=_key(),
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["payload"]["thread_id"]

    chat = client.post(
        f"/v2/ws/{ws_id}/threads/{thread_id}/messages",
        json={"text": "What does the stub resolver do here?"},
        headers=_key(),
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["reply"]["text"] == FAKE_REPLY_TEXT
    assert chat.json()["reply"]["in_reply_to"] == chat.json()["sent"]["message_id"]

    # --- 5. memo append -------------------------------------------------------
    memo = client.post(
        f"/v2/ws/{ws_id}/memo/entries",
        json={
            "actor": "learner",
            "body": "Revisit the stub resolver explanation before the lab.",
            "source": {"thread_id": thread_id},
        },
        headers=_key(),
    )
    assert memo.status_code == 201, memo.text

    # --- 6. start the lab artifact, then check it ----------------------------
    artifact = client.post(
        f"/v2/ws/{ws_id}/artifacts", json={"spec_id": LAB_SPEC_ID}, headers=_key()
    )
    assert artifact.status_code == 201, artifact.text
    artifact_id = artifact.json()["artifact_id"]

    lab_spec: Any = next(d for d in pack.documents["artifacts"].values() if d["id"] == LAB_SPEC_ID)
    for target in lab_spec["spec"]["checks"]:
        checked = client.post(
            f"/v2/ws/{ws_id}/artifacts/{artifact_id}/check",
            json={"check_id": target["check"], "params": dict(target["params"])},
            headers=_key(),
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["passed"] is True

    # --- 7. answer the artifact drill -> drill.judged gap ---------------------
    answered = client.post(
        f"/v2/ws/{ws_id}/drills/{DRILL_ITEM_ID}/answers",
        json={"artifact_id": artifact_id},
        headers=_key(),
    )
    assert answered.status_code == 201, answered.text
    # Every target check passed, so the judge's gap has nothing missing (#65,
    # #124): still a real ``drill.judged`` event, not an empty/absent one.
    assert answered.json()["judgment_status"] == "complete"

    answers = client.get(f"/v2/ws/{ws_id}/drills/answers")
    assert answers.status_code == 200
    assert answers.json()["items"] == [
        {
            "item_id": DRILL_ITEM_ID,
            "answer_event_id": answered.json()["id"],
            "judgment_status": "judged",
            "gap": {"missing": []},
        }
    ]

    # --- events v2: stored in the expected order, by position ----------------
    events = store.read(user_id=user_id)
    assert [e.type for e in events] == [
        "session.created",
        "ws.created",
        "highlight.created",
        "thread.created",
        "chat.sent",
        "chat.replied",
        "memo.appended",
        "artifact.started",
        "artifact.checked",
        "artifact.checked",
        "artifact.checked",
        "drill.answered",
        "drill.judged",
    ]
    positions = [e.position for e in events]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)

    # --- rebuild reproduces the same view documents (ADR-0008) ----------------
    before = (
        client.get(f"/v2/sessions/{session_id}").json(),
        client.get(f"/v2/ws/{ws_id}").json(),
        client.get(f"/v2/ws/{ws_id}/highlights").json(),
        client.get(f"/v2/ws/{ws_id}/threads").json(),
        client.get(f"/v2/ws/{ws_id}/threads/{thread_id}/messages").json(),
        client.get(f"/v2/ws/{ws_id}/memo/entries").json(),
        client.get(f"/v2/ws/{ws_id}/artifacts/{artifact_id}").json(),
        answers.json(),
    )
    replayed = rebuild_v2_views(InMemoryEventStore(), store)
    assert replayed == 0  # no v0.1 events; the v1 store passed in is empty
    after = (
        client.get(f"/v2/sessions/{session_id}").json(),
        client.get(f"/v2/ws/{ws_id}").json(),
        client.get(f"/v2/ws/{ws_id}/highlights").json(),
        client.get(f"/v2/ws/{ws_id}/threads").json(),
        client.get(f"/v2/ws/{ws_id}/threads/{thread_id}/messages").json(),
        client.get(f"/v2/ws/{ws_id}/memo/entries").json(),
        client.get(f"/v2/ws/{ws_id}/artifacts/{artifact_id}").json(),
        client.get(f"/v2/ws/{ws_id}/drills/answers").json(),
    )
    assert before == after, "rebuilding the v2 views changed what they read back"
