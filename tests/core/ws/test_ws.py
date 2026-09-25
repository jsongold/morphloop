"""ws resource core (#57): workspaces and threads, idempotent creation."""

from __future__ import annotations

import uuid

import pytest

from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.events_v2 import EventIdConflictError
from harness.core.ws import (
    WsNotFoundError,
    WsView,
    create_thread,
    create_ws,
    get_ws,
    list_ws,
)
from harness.testing.fakes_v2 import InMemoryEventStoreV2

USER = "usr_1"
SESSION = "ses_1"


@pytest.fixture
def store() -> InMemoryEventStoreV2:
    return InMemoryEventStoreV2(ContractSchemas.load())


def test_create_ws_appends_event_and_view_with_origin_label(store: InMemoryEventStoreV2) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        event = create_ws(
            tx, event_id=event_id, user_id=USER, session_id=SESSION, labels=["topic:x"]
        )
    assert (event.type, event.user_id, event.session_id) == ("ws.created", USER, SESSION)
    assert set(event.payload["labels"]) == {"topic:x", "origin:learner"}  # type: ignore[arg-type]
    with store.transaction() as tx:
        doc = get_ws(tx, event.ws_id, user_id=USER)
    assert doc == {
        "ws_id": event.ws_id,
        "user_id": USER,
        "session_id": SESSION,
        "labels": ["topic:x", "origin:learner"],
        "created_at": event.to_dict()["created_at"],
    }


def test_actor_system_sets_origin_app(store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        event = create_ws(
            tx, event_id=str(uuid.uuid4()), user_id=USER, session_id=SESSION, actor="system"
        )
    assert event.payload["labels"] == ["origin:app"]


def test_caller_supplied_origin_label_is_dropped(store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        event = create_ws(
            tx,
            event_id=str(uuid.uuid4()),
            user_id=USER,
            session_id=SESSION,
            labels=["origin:app"],
        )
    assert event.payload["labels"] == ["origin:learner"]


def test_resend_of_the_same_event_id_returns_the_same_ws(store: InMemoryEventStoreV2) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        first = create_ws(tx, event_id=event_id, user_id=USER, session_id=SESSION)
    with store.transaction() as tx:
        second = create_ws(tx, event_id=event_id, user_id=USER, session_id=SESSION)
    assert first.ws_id == second.ws_id
    assert len(store.read(user_id=USER)) == 1


def test_resend_with_a_different_session_conflicts(store: InMemoryEventStoreV2) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        create_ws(tx, event_id=event_id, user_id=USER, session_id=SESSION)
    with pytest.raises(EventIdConflictError), store.transaction() as tx:
        create_ws(tx, event_id=event_id, user_id=USER, session_id="ses_2")


def test_get_and_list_ws(store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        a = create_ws(tx, event_id=str(uuid.uuid4()), user_id=USER, session_id=SESSION)
        b = create_ws(tx, event_id=str(uuid.uuid4()), user_id=USER, session_id="ses_2")
    with store.transaction() as tx:
        assert {d["ws_id"] for d in list_ws(tx, user_id=USER)} == {a.ws_id, b.ws_id}
        assert [d["ws_id"] for d in list_ws(tx, user_id=USER, session_id=SESSION)] == [a.ws_id]
        with pytest.raises(WsNotFoundError):
            get_ws(tx, "ws_nope", user_id=USER)
        with pytest.raises(WsNotFoundError):
            get_ws(tx, a.ws_id, user_id="usr_other")


def test_create_thread_needs_an_existing_ws(store: InMemoryEventStoreV2) -> None:
    with pytest.raises(WsNotFoundError), store.transaction() as tx:
        create_thread(tx, event_id=str(uuid.uuid4()), user_id=USER, ws_id="ws_nope")


def test_create_thread_carries_the_wss_session_target_and_labels(
    store: InMemoryEventStoreV2,
) -> None:
    with store.transaction() as tx:
        ws = create_ws(tx, event_id=str(uuid.uuid4()), user_id=USER, session_id=SESSION)
    target = {"kind": "memo_entry", "entry_id": "ent_1"}
    with store.transaction() as tx:
        thread = create_thread(
            tx,
            event_id=str(uuid.uuid4()),
            user_id=USER,
            ws_id=ws.ws_id,
            target=target,
            labels=["mode:hint"],
        )
    assert thread.type == "thread.created"
    assert thread.session_id == SESSION and thread.ws_id == ws.ws_id
    assert thread.payload["target"] == target
    assert thread.payload["labels"] == ["mode:hint"]
    assert thread.payload["thread_id"].startswith("thr_")  # type: ignore[union-attr]


def test_thread_with_no_target_is_the_main_thread(store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        ws = create_ws(tx, event_id=str(uuid.uuid4()), user_id=USER, session_id=SESSION)
    with store.transaction() as tx:
        thread = create_thread(tx, event_id=str(uuid.uuid4()), user_id=USER, ws_id=ws.ws_id)
    assert "target" not in thread.payload
    assert thread.payload["labels"] == []


def test_resend_of_the_same_thread_event_id_returns_the_same_thread(
    store: InMemoryEventStoreV2,
) -> None:
    with store.transaction() as tx:
        ws = create_ws(tx, event_id=str(uuid.uuid4()), user_id=USER, session_id=SESSION)
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        first = create_thread(tx, event_id=event_id, user_id=USER, ws_id=ws.ws_id)
    with store.transaction() as tx:
        second = create_thread(tx, event_id=event_id, user_id=USER, ws_id=ws.ws_id)
    assert first.payload["thread_id"] == second.payload["thread_id"]
    assert len(store.read(ws_id=ws.ws_id)) == 2  # ws.created + one thread.created


def test_ws_view_is_registered() -> None:
    assert WsView.name == "ws"
