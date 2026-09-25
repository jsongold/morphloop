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
