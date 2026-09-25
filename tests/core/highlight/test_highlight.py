"""Tests for the highlight resource core (#34, #60): anchor, view, service."""

from __future__ import annotations

import pytest

from harness.core.contract_schemas import ContractSchemas
from harness.core.highlight.anchor import AnchorError, check_text_position
from harness.core.highlight.service import (
    HighlightNotFoundError,
    create_highlight,
    list_highlights,
    remove_highlight,
)
from harness.core.highlight.view import HighlightView, active_highlights, highlight_key
from harness.core.labels import LabelError
from harness.core.ports.json_types import JsonObject
from harness.testing.fakes_v2 import InMemoryEventStoreV2

WS_ID = "ws_01"
VOCAB = frozenset({"concept", "difficulty:easy"})
TOPIC_IDS = ("network", "network.dns")


def _anchor(start: int = 0, end: int = 5) -> JsonObject:
    return {
        "doc_id": "dns-resolution",
        "block_id": "intro",
        "selector": [
            {"type": "TextQuoteSelector", "exact": "hello"},
            {"type": "TextPositionSelector", "start": start, "end": end},
        ],
    }


@pytest.fixture
def schemas() -> ContractSchemas:
    return ContractSchemas.load()


# --- anchor ------------------------------------------------------------


def test_check_text_position_accepts_start_before_end() -> None:
    check_text_position(0, 5)


@pytest.mark.parametrize(("start", "end"), [(5, 5), (6, 5)])
def test_check_text_position_rejects_start_not_before_end(start: int, end: int) -> None:
    with pytest.raises(AnchorError, match="start"):
        check_text_position(start, end)


# --- service / view ------------------------------------------------------


def test_create_highlight_stores_a_view_document(schemas: ContractSchemas) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        doc = create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000001",
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=["concept"],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    assert doc["ws_id"] == WS_ID
    assert doc["anchor"] == _anchor()
    assert doc["labels"] == ["concept"]
    assert doc["removed"] is False
    highlight_id = doc["highlight_id"]
    assert isinstance(highlight_id, str) and highlight_id.startswith("hl_")

    with store.transaction() as tx:
        assert list_highlights(tx, WS_ID) == [doc]
        assert HighlightView.get(tx, highlight_key(WS_ID, highlight_id)) == doc


def test_create_highlight_rejects_a_label_outside_the_vocabulary(
    schemas: ContractSchemas,
) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx, pytest.raises(LabelError):
        create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000002",
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=["not-a-real-label"],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    with store.transaction() as tx:
        assert list_highlights(tx, WS_ID) == []


def test_remove_highlight_tombstones_it(schemas: ContractSchemas) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        doc = create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000003",
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    highlight_id = doc["highlight_id"]
    assert isinstance(highlight_id, str)

    with store.transaction() as tx:
        remove_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000004",
            user_id="usr_01",
            ws_id=WS_ID,
            highlight_id=highlight_id,
        )
    with store.transaction() as tx:
        assert list_highlights(tx, WS_ID) == []
        assert HighlightView.get(tx, highlight_key(WS_ID, highlight_id)) == {
            "highlight_id": highlight_id,
            "ws_id": WS_ID,
            "removed": True,
        }


def test_remove_highlight_unknown_id_is_not_found(schemas: ContractSchemas) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx, pytest.raises(HighlightNotFoundError):
        remove_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000005",
            user_id="usr_01",
            ws_id=WS_ID,
            highlight_id="hl_missing",
        )


def test_remove_highlight_twice_is_not_found(schemas: ContractSchemas) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        doc = create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000006",
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    highlight_id = doc["highlight_id"]
    assert isinstance(highlight_id, str)
    with store.transaction() as tx:
        remove_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000009",
            user_id="usr_01",
            ws_id=WS_ID,
            highlight_id=highlight_id,
        )
    with store.transaction() as tx, pytest.raises(HighlightNotFoundError):
        remove_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-00000000000a",
            user_id="usr_01",
            ws_id=WS_ID,
            highlight_id=highlight_id,
        )


def test_create_highlight_replays_a_retried_post(schemas: ContractSchemas) -> None:
    """Same Idempotency-Key + same body twice must return the same doc, not
    conflict on a random highlight_id (issue #60 review)."""
    store = InMemoryEventStoreV2(schemas)
    event_id = "0190f5a2-7c3e-7d4b-8a1f-00000000000b"
    kwargs: dict[str, object] = dict(
        event_id=event_id,
        user_id="usr_01",
        ws_id=WS_ID,
        anchor=_anchor(),
        labels=["concept"],
        label_vocabulary=VOCAB,
        topic_ids=TOPIC_IDS,
    )
    with store.transaction() as tx:
        first = create_highlight(tx, **kwargs)  # type: ignore[arg-type]
    with store.transaction() as tx:
        second = create_highlight(tx, **kwargs)  # type: ignore[arg-type]
    assert first == second


def test_remove_highlight_replays_a_retried_delete_after_tombstone(
    schemas: ContractSchemas,
) -> None:
    """Same Idempotency-Key resend of a DELETE must succeed again even though
    the highlight is now tombstoned (issue #60 review)."""
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        doc = create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-00000000000c",
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    highlight_id = doc["highlight_id"]
    assert isinstance(highlight_id, str)
    remove_event_id = "0190f5a2-7c3e-7d4b-8a1f-00000000000d"
    with store.transaction() as tx:
        remove_highlight(
            tx, event_id=remove_event_id, user_id="usr_01", ws_id=WS_ID, highlight_id=highlight_id
        )
    # Resend: must not raise, even though the highlight is now tombstoned.
    with store.transaction() as tx:
        remove_highlight(
            tx, event_id=remove_event_id, user_id="usr_01", ws_id=WS_ID, highlight_id=highlight_id
        )


def test_active_highlights_are_in_creation_order(schemas: ContractSchemas) -> None:
    """List order is the event's position, never the highlight_id (uuid) key
    order (common rule, issue #87/#92)."""
    store = InMemoryEventStoreV2(schemas)
    # Chosen so the derived highlight_id keys sort in the OPPOSITE order from
    # creation: first-created has the lexically largest id.
    first_event_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    second_event_id = "00000000-0000-4000-8000-000000000000"
    with store.transaction() as tx:
        first = create_highlight(
            tx,
            event_id=first_event_id,
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
        second = create_highlight(
            tx,
            event_id=second_event_id,
            user_id="usr_01",
            ws_id=WS_ID,
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    with store.transaction() as tx:
        ids = [doc["highlight_id"] for doc in list_highlights(tx, WS_ID)]
    assert ids == [first["highlight_id"], second["highlight_id"]]


def test_active_highlights_scoped_by_ws_id(schemas: ContractSchemas) -> None:
    store = InMemoryEventStoreV2(schemas)
    with store.transaction() as tx:
        create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000007",
            user_id="usr_01",
            ws_id="ws_a",
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
        create_highlight(
            tx,
            event_id="0190f5a2-7c3e-7d4b-8a1f-000000000008",
            user_id="usr_01",
            ws_id="ws_b",
            anchor=_anchor(),
            labels=[],
            label_vocabulary=VOCAB,
            topic_ids=TOPIC_IDS,
        )
    with store.transaction() as tx:
        assert len(active_highlights(tx, "ws_a")) == 1
        assert len(active_highlights(tx, "ws_b")) == 1
