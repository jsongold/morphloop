"""PostgresEventStore against a real Postgres 16 (see conftest.py).

Mirrors the semantics of ``harness.testing.fakes.InMemoryEventStore``.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from harness.adapters.postgres import PostgresEventStore
from harness.core.ports import (
    AppendRequest,
    EventStore,
    EventStoreError,
    IdempotencyConflictError,
    JsonObject,
)

T0 = datetime(2026, 9, 21, 10, 0, 0, 123456, tzinfo=UTC)


def _uid() -> str:
    return uuid.uuid4().hex


def _request(
    *,
    session_id: str,
    key: str | None = None,
    payload: JsonObject | None = None,
    event_id: str | None = None,
    learner_id: str = "learner_1",
) -> AppendRequest:
    return AppendRequest(
        event_id=event_id or f"evt_{_uid()}",
        event_type="terminal.command",
        event_version=1,
        occurred_at=T0,
        idempotency_key=key,
        causation_id=None,
        correlation_id="corr_1",
        learner_id=learner_id,
        session_id=session_id,
        attempt_id="att_1",
        activity_definition_id="def_1",
        actor="learner",
        payload=payload if payload is not None else {"command": "dig", "args": ["a", 1, 2.5]},
    )


def test_conforms_to_port(store: PostgresEventStore) -> None:
    port: EventStore = store
    assert port is store


def test_append_assigns_increasing_positions_and_roundtrips(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    other = f"ses_{_uid()}"
    requests = [_request(session_id=session, key=f"k_{_uid()}") for _ in range(3)]
    with store.transaction() as tx:
        stored = [tx.append(requests[0]).event, tx.append(_request(session_id=other)).event]
    with store.transaction() as tx:
        stored += [tx.append(r).event for r in requests[1:]]

    positions = [e.position for e in stored]
    assert positions == sorted(positions) and len(set(positions)) == 4

    events = store.read_session(session)
    assert [e.event_id for e in events] == [r.event_id for r in requests]
    first = events[0]
    assert first.to_dict() == {
        **requests[0].to_dict(),
        "position": first.position,
        "recorded_at": first.to_dict()["recorded_at"],
    }
    assert first.occurred_at == T0
    assert first.recorded_at.tzinfo is not None


def test_read_ranges_and_limit(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    with store.transaction() as tx:
        stored = [tx.append(_request(session_id=session)).event for _ in range(4)]
    p = [e.position for e in stored]

    assert [e.position for e in store.read_session(session, after_position=p[0])] == p[1:]
    assert [e.position for e in store.read_session(session, until_position=p[2])] == p[:3]
    assert [e.position for e in store.read_session(session, limit=2)] == p[:2]
    page = store.read_all(after_position=p[0], until_position=p[3], limit=2)
    assert [e.position for e in page] == p[1:3]
    assert [e.position for e in store.read_all(after_position=p[3])] == []


def test_idempotent_resend_returns_existing_event(store: PostgresEventStore) -> None:
    """AC-F5: same key + same content -> existing event, no new row."""
    session = f"ses_{_uid()}"
    key = f"k_{_uid()}"
    with store.transaction() as tx:
        first = tx.append(_request(session_id=session, key=key))
    # A retrying producer may regenerate event_id and occurred_at.
    resend = _request(session_id=session, key=key)
    with store.transaction() as tx:
        second = tx.append(resend)
        same_tx = tx.append(resend)

    assert first.created is True
    assert second.created is False and same_tx.created is False
    assert second.event == first.event == same_tx.event
    assert len(store.read_session(session)) == 1


def test_same_key_different_content_raises_conflict(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    key = f"k_{_uid()}"
    with store.transaction() as tx:
        first = tx.append(_request(session_id=session, key=key))
    with store.transaction() as tx:
        with pytest.raises(IdempotencyConflictError) as info:
            tx.append(_request(session_id=session, key=key, payload={"command": "other"}))
        # The transaction is still usable after the conflict.
        tx.append(_request(session_id=session))
    assert info.value.existing == first.event
    assert info.value.idempotency_key == key
    assert len(store.read_session(session)) == 2


def test_duplicate_event_id_with_other_key_raises(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    event_id = f"evt_{_uid()}"
    with store.transaction() as tx:
        tx.append(_request(session_id=session, event_id=event_id))
    with store.transaction() as tx:
        with pytest.raises(EventStoreError):
            tx.append(_request(session_id=session, event_id=event_id, key=f"k_{_uid()}"))
        tx.put_projection("p", "still-usable", {"ok": True})
        assert tx.get_projection("p", "still-usable") == {"ok": True}
        tx.delete_projection("p", "still-usable")
    assert len(store.read_session(session)) == 1


def test_concurrent_appends_with_one_key_store_one_event(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    key = f"k_{_uid()}"
    barrier = threading.Barrier(4)
    results: list[bool] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            with store.transaction() as tx:
                results.append(tx.append(_request(session_id=session, key=key)).created)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert sorted(results) == [False, False, False, True]
    assert len(store.read_session(session)) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE learning_events SET event_type = 'x.y' WHERE session_id = :s",
        "DELETE FROM learning_events WHERE session_id = :s",
        "TRUNCATE learning_events",
    ],
)
def test_log_is_append_only_at_db_level(
    store: PostgresEventStore, pg_engine: Engine, statement: str
) -> None:
    session = f"ses_{_uid()}"
    with store.transaction() as tx:
        tx.append(_request(session_id=session))
    with pytest.raises(DBAPIError, match="append-only"):
        with pg_engine.begin() as conn:
            conn.execute(text(statement), {"s": session})
    assert len(store.read_session(session)) == 1


def test_rollback_discards_event_and_projection(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    key = f"k_{_uid()}"
    with pytest.raises(RuntimeError, match="boom"):
        with store.transaction() as tx:
            tx.append(_request(session_id=session, key=key))
            tx.put_projection("session_state", session, {"step": 1})
            raise RuntimeError("boom")

    assert store.read_session(session) == []
    with store.transaction() as tx:
        assert tx.get_projection("session_state", session) is None
        # The key was rolled back too, so it is free again.
        assert tx.append(_request(session_id=session, key=key)).created is True


def test_commit_persists_event_and_projection_together(store: PostgresEventStore) -> None:
    session = f"ses_{_uid()}"
    with store.transaction() as tx:
        event = tx.append(_request(session_id=session)).event
        tx.put_projection("session_state", session, {"last_position": event.position})
    with store.transaction() as tx:
        assert tx.get_projection("session_state", session) == {"last_position": event.position}
    assert [e.event_id for e in store.read_session(session)] == [event.event_id]


def test_transaction_object_unusable_after_block(store: PostgresEventStore) -> None:
    with store.transaction() as tx:
        pass
    with pytest.raises(EventStoreError):
        tx.get_projection("p", "k")


def test_projection_crud(store: PostgresEventStore) -> None:
    name = f"proj_{_uid()}"
    doc: JsonObject = {"a": 1, "b": [True, None, 1.5, "x"], "nested": {"k": "é"}}
    with store.transaction() as tx:
        assert tx.get_projection(name, "k1") is None
        assert tx.list_projection(name) == []
        tx.put_projection(name, "k1", doc)
        tx.put_projection(name, "k1", {"replaced": True})
        for key in ["b/2", "a/2", "a/1", "B/1", "a/10"]:
            tx.put_projection(name, key, {"key": key})
        tx.put_projection(f"{name}_other", "a/1", {"other": True})
        tx.put_projection(name, "doc", doc)

    with store.transaction() as tx:
        assert tx.get_projection(name, "k1") == {"replaced": True}
        assert tx.get_projection(name, "doc") == doc
        keys = [k for k, _ in tx.list_projection(name)]
        assert keys == sorted(["k1", "b/2", "a/2", "a/1", "B/1", "a/10", "doc"])
        assert tx.list_projection(name, key_prefix="a/") == [
            ("a/1", {"key": "a/1"}),
            ("a/10", {"key": "a/10"}),
            ("a/2", {"key": "a/2"}),
        ]
        # A LIKE wildcard in the prefix is matched literally.
        assert tx.list_projection(name, key_prefix="a%") == []

        tx.delete_projection(name, "a/1")
        tx.delete_projection(name, "missing")  # no-op
        assert tx.get_projection(name, "a/1") is None

        tx.clear_projection(name)
        assert tx.list_projection(name) == []
        assert tx.get_projection(f"{name}_other", "a/1") == {"other": True}


def test_lock_learner_serializes_same_learner_only(store: PostgresEventStore) -> None:
    learner = f"lrn_{_uid()}"
    other = f"lrn_{_uid()}"
    locked = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with store.transaction() as tx:
            tx.lock_learner(learner)
            tx.lock_learner(learner)  # re-lock in the same transaction: no-op
            locked.set()
            release.wait(timeout=30)

    def contender(learner_id: str, done: threading.Event) -> None:
        with store.transaction() as tx:
            tx.lock_learner(learner_id)
            done.set()

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert locked.wait(timeout=10)

    other_done = threading.Event()
    other_thread = threading.Thread(target=contender, args=(other, other_done))
    other_thread.start()
    assert other_done.wait(timeout=10), "a different learner must not be blocked"

    same_done = threading.Event()
    same_thread = threading.Thread(target=contender, args=(learner, same_done))
    same_thread.start()
    assert not same_done.wait(timeout=0.5), "same learner must wait for the holder"

    release.set()
    holder_thread.join(timeout=10)
    assert same_done.wait(timeout=10), "lock must be released when the holder commits"
    for t in (other_thread, same_thread):
        t.join(timeout=10)
