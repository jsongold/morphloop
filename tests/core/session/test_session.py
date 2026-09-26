"""Session resource: topic subtree lookup, creation and reads (#34, #54)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.cli.rebuild import rebuild
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.v2.importer import PackV2, import_pack_v2
from harness.core.ports.events_v2 import EventIdConflictError
from harness.core.session.model import SessionsByUserView, TopicNotFoundError, find_topic
from harness.core.session.service import (
    PackMismatchError,
    create_session,
    get_session,
    list_sessions,
    session_id_for,
)
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


@pytest.fixture(scope="module")
def pack() -> PackV2:
    return import_pack_v2(PACK_DIR, artifact_types=PACK_ARTIFACT_TYPES)


@pytest.fixture
def store() -> InMemoryEventStoreV2:
    return InMemoryEventStoreV2(ContractSchemas.load())


def test_find_topic_returns_the_matching_node_with_its_descendants(pack: PackV2) -> None:
    topic = find_topic(pack.topics, "network.dns")
    assert topic["id"] == "network.dns"
    assert [child["id"] for child in topic["topics"]] == [
        "network.dns.resolution",
        "network.dns.records",
    ]


def test_find_topic_finds_a_leaf(pack: PackV2) -> None:
    topic = find_topic(pack.topics, "network.dns.resolution")
    assert topic == {
        "id": "network.dns.resolution",
        "title": "Resolution",
        "docs": ("dns-resolution",),
    }


def test_find_topic_raises_for_an_unknown_id(pack: PackV2) -> None:
    with pytest.raises(TopicNotFoundError):
        find_topic(pack.topics, "nope")


def test_create_session_pins_topic_subtree_and_pack_identity(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        doc = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network.dns.resolution",
        )
    assert doc["id"] == session_id_for(event_id)
    assert doc["user_id"] == "usr_alice"
    assert doc["pack_id"] == pack.pack_id
    assert doc["pack_version"] == pack.pack_version
    assert doc["pack_content_hash"] == pack.pack_hash
    assert doc["topic_id"] == "network.dns.resolution"
    assert doc["tree"] == {
        "id": "network.dns.resolution",
        "title": "Resolution",
        "docs": ["dns-resolution"],
    }
    assert "created_at" in doc


def test_create_session_rejects_a_pack_id_that_is_not_the_loaded_pack(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    with pytest.raises(PackMismatchError), store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id="some-other-pack",
            topic_id="network",
        )
    assert not store.read()


def test_create_session_rejects_an_unknown_topic(pack: PackV2, store: InMemoryEventStoreV2) -> None:
    with pytest.raises(TopicNotFoundError), store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="nope",
        )
    assert not store.read()


def test_resend_of_the_same_event_id_and_body_returns_the_same_session(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        first = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with store.transaction() as tx:
        second = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network",
        )
    assert first == second
    assert len(store.read()) == 1


def test_reused_event_id_with_a_different_body_conflicts(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with pytest.raises(EventIdConflictError), store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )


def test_get_session_returns_none_when_missing(store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        assert get_session(tx, "ses_missing", user_id="usr_alice") is None


def test_get_session_of_another_user_returns_none(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    with store.transaction() as tx:
        created = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with store.transaction() as tx:
        assert get_session(tx, created["id"], user_id="usr_bob") is None


def test_list_sessions_returns_every_created_session(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    with store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns.resolution",
        )
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns.records",
        )
    with store.transaction() as tx:
        topic_ids = {doc["topic_id"] for doc in list_sessions(tx, user_id="usr_alice", limit=50)[0]}
    assert topic_ids == {"network.dns.resolution", "network.dns.records"}


def test_list_sessions_excludes_other_users(pack: PackV2, store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with store.transaction() as tx:
        assert list_sessions(tx, user_id="usr_bob", limit=50) == ([], None)


def test_list_sessions_is_in_creation_order_not_uuid_order(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    # A UUID sort would put "aaaa..." before "ffff...", opposite of creation order.
    first_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    second_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with store.transaction() as tx:
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=first_id,
            pack_id=pack.pack_id,
            topic_id="network.dns.resolution",
        )
        create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=second_id,
            pack_id=pack.pack_id,
            topic_id="network.dns.records",
        )
    with store.transaction() as tx:
        topic_ids = [doc["topic_id"] for doc in list_sessions(tx, user_id="usr_alice", limit=50)[0]]
    assert topic_ids == ["network.dns.resolution", "network.dns.records"]


def test_resend_after_the_pack_changed_still_returns_the_same_session(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    """A server restart with an updated pack must not turn an identical
    resend into a 404/409 (#89 review)."""
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        first = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network",
        )
    updated_pack = replace(pack, pack_version="9.9.9", pack_hash="sha256:" + "0" * 64, topics=())
    with store.transaction() as tx:
        second = create_session(
            tx,
            pack=updated_pack,
            user_id="usr_alice",
            event_id=event_id,
            pack_id=pack.pack_id,
            topic_id="network",
        )
    assert second == first


def test_list_sessions_pages_in_creation_order(pack: PackV2, store: InMemoryEventStoreV2) -> None:
    with store.transaction() as tx:
        ids = [
            create_session(
                tx,
                pack=pack,
                user_id="usr_alice",
                event_id=str(uuid.uuid4()),
                pack_id=pack.pack_id,
                topic_id="network",
            )["id"]
            for _ in range(3)
        ]
        # "usr_alicex" shares the raw string prefix "usr_alice"
        create_session(
            tx,
            pack=pack,
            user_id="usr_alicex",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with store.transaction() as tx:
        first, cursor = list_sessions(tx, user_id="usr_alice", limit=2)
        second, end = list_sessions(tx, user_id="usr_alice", after=cursor, limit=2)
    assert [doc["id"] for doc in first + second] == ids
    assert cursor is not None and end is None


def test_rebuild_fills_the_session_index_from_the_log(
    pack: PackV2, store: InMemoryEventStoreV2
) -> None:
    with store.transaction() as tx:
        doc = create_session(
            tx,
            pack=pack,
            user_id="usr_alice",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network",
        )
    with store.transaction() as tx:
        tx.clear_view(SessionsByUserView.name)
        assert list_sessions(tx, user_id="usr_alice", limit=50) == ([], None)
    assert rebuild(store) == len(store.read())  # `morphloop rebuild`
    with store.transaction() as tx:
        assert [d["id"] for d in list_sessions(tx, user_id="usr_alice", limit=50)[0]] == [doc["id"]]
