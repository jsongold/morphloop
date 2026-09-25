"""memo entries: append-once idempotency, label vocabulary, append order (#59)."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from harness.api.v2.deps import replay_or_conflict
from harness.core.contract_schemas import ContractSchemas
from harness.core.labels import LabelError
from harness.core.memo.entries import (
    MemoEntries,
    append_memo_entry,
    build_memo_appended,
    entry_for,
    entry_id_for,
)
from harness.core.pack.v2.importer import PackV2, import_pack_v2
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK_DIR = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


@pytest.fixture
def pack() -> PackV2:
    return import_pack_v2(PACK_DIR)


@pytest.fixture
def store() -> InMemoryEventStoreV2:
    return InMemoryEventStoreV2(ContractSchemas.load())


def _event_id() -> str:
    return str(uuid.uuid4())


def _append(
    store: InMemoryEventStoreV2,
    pack: PackV2,
    *,
    event_id: str | None = None,
    ws_id: str = "ws_01",
    session_id: str = "ses_01",
    actor: str = "learner",
    body: str = "note",
    source: dict[str, str] | None = None,
    labels: Iterator[str] | list[str] = (),
) -> dict[str, Any]:
    """Build the candidate and run it through the same replay-then-append flow
    the route uses (`replay_or_conflict` before any pack lookup)."""
    candidate = build_memo_appended(
        event_id=event_id or _event_id(),
        user_id="usr_01",
        ws_id=ws_id,
        session_id=session_id,
        actor=actor,  # type: ignore[arg-type]
        body=body,
        source=source,
        labels=list(labels),
    )
    with store.transaction() as tx:
        stored = replay_or_conflict(tx, candidate)
        if stored is not None:
            return entry_for(tx, stored)
        return append_memo_entry(tx, candidate=candidate, pack=pack)


def test_entry_id_is_derived_from_event_id_and_stable() -> None:
    event_id = "0190f5a2-7c3e-7d4b-8a1f-2b3c4d5e6f70"
    expected = f"ent_{uuid.UUID(event_id).hex}"
    assert entry_id_for(event_id) == expected
    assert entry_id_for(event_id) == entry_id_for(event_id)


def test_append_stores_and_returns_the_entry(store: InMemoryEventStoreV2, pack: PackV2) -> None:
    event_id = _event_id()
    entry = _append(store, pack, event_id=event_id, body="DNS caches negative answers too.")

    assert entry["entry_id"] == entry_id_for(event_id)
    assert entry["ws_id"] == "ws_01"
    assert entry["actor"] == "learner"
    assert entry["body"] == "DNS caches negative answers too."
    assert entry["labels"] == []
    assert "source" not in entry

    with store.transaction() as tx:
        assert MemoEntries.list_for_ws(tx, "ws_01") == [entry]


def test_resend_of_the_same_event_id_appends_once(
    store: InMemoryEventStoreV2, pack: PackV2
) -> None:
    event_id = _event_id()
    first = _append(store, pack, event_id=event_id, body="same note")
    second = _append(store, pack, event_id=event_id, body="same note")

    assert first == second
    with store.transaction() as tx:
        assert len(MemoEntries.list_for_ws(tx, "ws_01")) == 1


def test_unknown_label_raises_and_appends_nothing(
    store: InMemoryEventStoreV2, pack: PackV2
) -> None:
    with pytest.raises(LabelError):
        _append(store, pack, labels=["not-a-real-label"])

    assert store.read(ws_id="ws_01") == []
    with store.transaction() as tx:
        assert MemoEntries.list_for_ws(tx, "ws_01") == []


def test_resend_after_label_removed_from_pack_still_replays(
    store: InMemoryEventStoreV2, pack: PackV2
) -> None:
    """A resend replays the stored entry without re-checking labels (Codex review,
    PR #87): a pack redeploy that drops a label must not turn an old retry into 422."""
    event_id = _event_id()
    first = _append(store, pack, event_id=event_id, labels=["concept"])
    stale_pack = dataclasses.replace(pack, labels=frozenset())
    second = _append(store, stale_pack, event_id=event_id, labels=["concept"])
    assert first == second


def test_labels_in_the_pack_vocabulary_are_kept(store: InMemoryEventStoreV2, pack: PackV2) -> None:
    entry = _append(store, pack, labels=["concept", "difficulty:hard"])
    assert entry["labels"] == ["concept", "difficulty:hard"]


def test_list_for_ws_is_append_order_and_scoped_to_one_ws(
    store: InMemoryEventStoreV2, pack: PackV2
) -> None:
    first = _append(store, pack, ws_id="ws_a", body="a")
    _append(store, pack, ws_id="ws_b", actor="assistant", body="other ws")
    second = _append(store, pack, ws_id="ws_a", actor="assistant", body="b")

    with store.transaction() as tx:
        assert MemoEntries.list_for_ws(tx, "ws_a") == [first, second]


def test_source_is_carried_through(store: InMemoryEventStoreV2, pack: PackV2) -> None:
    entry = _append(store, pack, source={"highlight_id": "hl_abcdefgh"})
    assert entry["source"] == {"highlight_id": "hl_abcdefgh"}
