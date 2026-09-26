"""Notebook workspace composition on the event transaction."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.notebook.build import build_workspace
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.events_v2 import EventTransactionV2, StoredEventV2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.session.service import PackMismatchError, create_session
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


class _HideFirstWsGet:
    """Transaction whose first ``get(ws_event_id)`` reports the committed ws
    event absent, modelling a concurrent build committing between the replay
    lookup and the content load (the window #117 flagged)."""

    def __init__(self, inner: EventTransactionV2, ws_event_id: str) -> None:
        self._inner = inner
        self._ws_event_id = ws_event_id
        self._hidden = False

    def get(self, event_id: str) -> StoredEventV2 | None:
        if event_id == self._ws_event_id and not self._hidden:
            self._hidden = True
            return None
        return self._inner.get(event_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _HideFirstWsGetStore:
    def __init__(self, inner: InMemoryEventStoreV2, ws_event_id: str) -> None:
        self._inner = inner
        self._ws_event_id = ws_event_id

    @contextmanager
    def transaction(self) -> Iterator[EventTransactionV2]:
        with self._inner.transaction() as tx:
            yield _HideFirstWsGet(tx, self._ws_event_id)


def test_build_selects_generated_docs_and_checks_session_owner() -> None:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    generated.add(
        GeneratedDocument(
            resource="textbook",
            id="generated-doc",
            body={
                "id": "generated-doc",
                "title": "Generated lesson",
                "labels": ["concept"],
                "blocks": [{"id": "b1", "body": "Generated text", "labels": []}],
            },
            provenance={},
        )
    )
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        params = {
            "pack": pack,
            "generated": generated,
            "event_id": str(uuid.uuid4()),
            "session_id": str(session["id"]),
            "labels": ["concept"],
        }
        result = build_workspace(tx, user_id="usr_local", **params)
        assert any(doc["id"] == "generated-doc" for doc in result["documents"])
        with pytest.raises(LookupError):
            build_workspace(tx, user_id="usr_other", **{**params, "event_id": str(uuid.uuid4())})


def test_build_rejects_a_session_pinned_to_a_different_pack() -> None:
    """#117 P1: a session whose pinned pack revision differs from the loaded
    pack is rejected before content is selected from the current pack."""
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        for changed in (
            replace(pack, pack_id="different-pack"),
            replace(pack, pack_version="different-version"),
            replace(pack, pack_hash="different-hash"),
        ):
            with pytest.raises(PackMismatchError):
                build_workspace(
                    tx,
                    pack=changed,
                    generated=generated,
                    event_id=str(uuid.uuid4()),
                    user_id="usr_local",
                    session_id=str(session["id"]),
                    labels=["concept"],
                )


def test_build_excludes_generated_content_from_another_pack() -> None:
    """#117 P1: generated content recorded under another pack revision is not
    selected into a workspace pinned to the loaded pack."""
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    other_pack = {"pack_id": "other-pack", "pack_hash": "other-hash"}
    generated.add(
        GeneratedDocument(
            resource="drill",
            id="old-drill",
            body={
                "id": "old-drill",
                "question": "Old question",
                "expected": "old",
                "answer_mode": "text",
                "labels": ["concept"],
            },
            provenance=other_pack,
        )
    )
    generated.add(
        GeneratedDocument(
            resource="textbook",
            id="old-doc",
            body={
                "id": "old-doc",
                "title": "Old lesson",
                "labels": ["concept"],
                "blocks": [{"id": "b1", "body": "Old text", "labels": []}],
            },
            provenance=other_pack,
        )
    )
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        result = build_workspace(
            tx,
            pack=pack,
            generated=generated,
            event_id=str(uuid.uuid4()),
            user_id="usr_local",
            session_id=str(session["id"]),
            labels=["concept"],
        )
    assert all(item["id"] != "old-drill" for item in result["drills"])
    assert all(doc["id"] != "old-doc" for doc in result["documents"])


def test_build_excludes_pack_holdout_drills() -> None:
    """#117 P1: the notebook must not reveal a pack drill marked ``sys:holdout``."""
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        result = build_workspace(
            tx,
            pack=pack,
            generated=generated,
            event_id=str(uuid.uuid4()),
            user_id="usr_local",
            session_id=str(session["id"]),
            topic="network.dns.resolution",
        )
    ids = {item["id"] for item in result["drills"]}
    assert "dns-resolver-text" in ids
    assert "dns-fix-resolver-lab" not in ids


def test_build_replay_is_race_safe_when_selection_appears() -> None:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    store = InMemoryEventStoreV2(ContractSchemas.load())
    event_id = str(uuid.uuid4())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        params = {
            "pack": pack,
            "generated": generated,
            "event_id": event_id,
            "session_id": str(session["id"]),
            "labels": ["concept"],
        }
        first = build_workspace(tx, user_id="usr_local", **params)
    # The resend's first lookup misses the (concurrently committed) ws event,
    # but the paired notebook.built selection is visible: a re-read must
    # recover the replay instead of 409ing an identical request.
    with _HideFirstWsGetStore(store, event_id).transaction() as tx:
        replay = build_workspace(tx, user_id="usr_local", **params)
    assert replay == first
