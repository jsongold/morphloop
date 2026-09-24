"""EventStoreV2 contract suite, run against Postgres and the in-memory fake.

The Postgres log is shared and append-only, so every test uses fresh ids and
reads filtered by its own ``user_id``.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from harness.adapters.postgres.event_store_v2 import PostgresEventStoreV2
from harness.core.contract_schemas import (
    EVENT_V2_STORED_ID,
    ContractSchemas,
    ContractValidationError,
)
from harness.core.ports.events_v2 import (
    EventIdConflictError,
    EventStoreV2,
    EventStoreV2Error,
    EventTransactionV2,
    EventV2,
)
from harness.testing.fakes_v2 import (
    PROBE_EVENT_TYPE,
    InMemoryEventStoreV2,
    contract_schemas_with_probe,
)


def _uid() -> str:
    return uuid.uuid4().hex


def _event(
    user_id: str,
    *,
    note: str = "hi",
    session_id: str | None = None,
    ws_id: str | None = None,
    id: str | None = None,
) -> EventV2:
    return EventV2(
        id=id or str(uuid.uuid4()),
        type=PROBE_EVENT_TYPE,
        actor="learner",
        user_id=user_id,
        session_id=session_id,
        ws_id=ws_id,
        payload={"note": note},
    )


@pytest.fixture(scope="module")
def schemas(tmp_path_factory: pytest.TempPathFactory) -> ContractSchemas:
    return contract_schemas_with_probe(tmp_path_factory.mktemp("contracts"))


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest, schemas: ContractSchemas) -> EventStoreV2:
    if request.param == "memory":
        return InMemoryEventStoreV2(schemas)
    return PostgresEventStoreV2(request.getfixturevalue("pg_engine"), schemas)


@pytest.fixture
def user() -> str:
    return f"usr_{_uid()}"


def test_append_assigns_increasing_positions_and_roundtrips(
    store: EventStoreV2, user: str, schemas: ContractSchemas
) -> None:
    events = [_event(user, session_id="ses_1", ws_id="ws_1"), _event(user), _event(user)]
    with store.transaction() as tx:
        stored = [tx.append(events[0]).event, tx.append(events[1]).event]
    with store.transaction() as tx:
        stored.append(tx.append(events[2]).event)

    positions = [e.position for e in stored]
    assert positions == sorted(set(positions))
    read = store.read(user_id=user)
    assert read == stored
    first = read[0]
    assert first.to_dict() == {
        **events[0].to_dict(),
        "position": first.position,
        "created_at": first.to_dict()["created_at"],
    }
    schemas.validate(first.to_dict(), EVENT_V2_STORED_ID)


def test_resend_with_same_id_returns_existing(store: EventStoreV2, user: str) -> None:
    event = _event(user)
    with store.transaction() as tx:
        first = tx.append(event)
    with store.transaction() as tx:
        again = tx.append(event)
    assert first.created and not again.created
    assert again.event == first.event
    assert len(store.read(user_id=user)) == 1


def test_same_id_different_content_conflicts(store: EventStoreV2, user: str) -> None:
    event = _event(user)
    with store.transaction() as tx:
        stored = tx.append(event).event
    with pytest.raises(EventIdConflictError) as info, store.transaction() as tx:
        tx.append(_event(user, id=event.id, note="changed"))
    assert info.value.existing == stored


def test_invalid_event_is_rejected_and_not_stored(store: EventStoreV2, user: str) -> None:
    bad = EventV2(
        id=str(uuid.uuid4()), type="unknown.created", actor="learner", user_id=user, payload={}
    )
    with pytest.raises(ContractValidationError), store.transaction() as tx:
        tx.append(bad)
    with pytest.raises(ContractValidationError), store.transaction() as tx:
        tx.append(_event(user, note=1))  # type: ignore[arg-type]
    assert store.read(user_id=user) == []


def test_read_filters_and_pages(store: EventStoreV2, user: str) -> None:
    session, ws = f"ses_{_uid()}", f"ws_{_uid()}"
    with store.transaction() as tx:
        a = tx.append(_event(user, session_id=session, ws_id=ws)).event
        b = tx.append(_event(user, session_id=session)).event
        c = tx.append(_event(user)).event
        tx.append(_event(f"usr_{_uid()}", session_id=session))
    assert store.read(user_id=user) == [a, b, c]
    assert store.read(user_id=user, session_id=session) == [a, b]
    assert store.read(ws_id=ws) == [a]
    assert store.read(user_id=user, after_position=a.position, limit=1) == [b]
    rest = store.read(after_position=b.position)
    assert c in rest and b not in rest


def test_append_and_views_commit_and_roll_back_together(store: EventStoreV2, user: str) -> None:
    view = f"view_{_uid()}"
    with store.transaction() as tx:
        tx.append(_event(user))
        tx.put_view(view, "b", {"n": 1})
        tx.put_view(view, "a", {"n": [1, 2.5, None]})
        tx.put_view(view, "b", {"n": 2})
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.append(_event(user))
        tx.put_view(view, "c", {"n": 3})
        raise RuntimeError("boom")
    assert len(store.read(user_id=user)) == 1
    with store.transaction() as tx:
        assert tx.get_view(view, "b") == {"n": 2}
        assert tx.get_view(view, "c") is None
        assert tx.list_view(view) == [("a", {"n": [1, 2.5, None]}), ("b", {"n": 2})]
        assert tx.list_view(view, key_prefix="b") == [("b", {"n": 2})]
        tx.clear_view(view)
        assert tx.list_view(view) == []


def test_transaction_cannot_be_used_after_it_ends(store: EventStoreV2, user: str) -> None:
    with store.transaction() as tx:
        pass
    ended: EventTransactionV2 = tx
    with pytest.raises(EventStoreV2Error):
        ended.append(_event(user))


def test_concurrent_resends_store_one_event(store: EventStoreV2, user: str) -> None:
    event = _event(user)
    results: list[bool] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        with store.transaction() as tx:
            results.append(tx.append(event).created)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, False, False, True]
    assert len(store.read(user_id=user)) == 1


_SEED = {
    "events_v2": "INSERT INTO events_v2 (id, type, actor, user_id, payload) "
    "VALUES (gen_random_uuid()::text, 'x.y', 'system', 'usr_1', '{}')",
    "generated_documents": "INSERT INTO generated_documents (resource, id, body, provenance) "
    "VALUES ('drill', gen_random_uuid()::text, '{}', '{}')",
}


@pytest.mark.parametrize(
    ("table", "sql"),
    [
        ("events_v2", "UPDATE events_v2 SET type = 'x.z'"),
        ("events_v2", "DELETE FROM events_v2"),
        ("events_v2", "TRUNCATE events_v2"),
        ("generated_documents", "UPDATE generated_documents SET labels = '{a}'"),
    ],
)
def test_postgres_rejects_mutation(pg_engine: Engine, table: str, sql: str) -> None:
    with pytest.raises(DBAPIError, match="not allowed"), pg_engine.begin() as conn:
        conn.execute(text(_SEED[table]))  # rolled back with the failing statement
        conn.execute(text(sql))
