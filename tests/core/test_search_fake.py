"""InMemorySearchIndex: upsert/delete by (kind, id), learner scope, parent_id (#180 S3a)."""

from __future__ import annotations

import pytest

from harness.core.ports.search import KeywordSearchRequest, SearchDocument
from harness.testing.search import InMemorySearchIndex


def _req(
    text: str, user_id: str = "u1", limit: int = 10, resources: tuple[str, ...] = ()
) -> KeywordSearchRequest:
    return KeywordSearchRequest(text=text, user_id=user_id, limit=limit, resources=resources)


@pytest.fixture
def index() -> InMemorySearchIndex:
    idx = InMemorySearchIndex()
    idx.upsert(
        [
            SearchDocument(kind="textbook_block", id="b1", parent_id="doc1", text="DNS TTL ttl"),
            SearchDocument(
                kind="memo_entry", id="m1", parent_id="ws1", owner_user_id="u1", text="my ttl note"
            ),
            SearchDocument(
                kind="memo_entry",
                id="m2",
                parent_id="ws2",
                owner_user_id="u2",
                text="their ttl note",
            ),
        ]
    )
    return idx


def test_only_shared_and_own_rows_are_returned(index: InMemorySearchIndex) -> None:
    assert [(h.kind, h.id) for h in index.search(_req("ttl"))] == [
        ("textbook_block", "b1"),
        ("memo_entry", "m1"),
    ]
    assert {h.id for h in index.search(_req("ttl", user_id="u2"))} == {"b1", "m2"}


def test_hits_carry_parent_id_and_keyword_source(index: InMemorySearchIndex) -> None:
    hit = index.search(_req("my ttl"))[0]
    assert (hit.parent_id, hit.source) == ("ws1", "keyword")


def test_upsert_replaces_by_kind_and_id(index: InMemorySearchIndex) -> None:
    index.upsert([SearchDocument(kind="memo_entry", id="m1", owner_user_id="u1", text="renamed")])
    assert index.search(_req("my ttl")) == []
    assert [h.id for h in index.search(_req("renamed"))] == ["m1"]


def test_delete_is_idempotent(index: InMemorySearchIndex) -> None:
    index.delete("memo_entry", "m1")
    index.delete("memo_entry", "m1")
    assert {h.id for h in index.search(_req("ttl"))} == {"b1"}


def test_resources_filter_and_limit(index: InMemorySearchIndex) -> None:
    assert [h.id for h in index.search(_req("ttl", resources=("memo_entry",)))] == ["m1"]
    assert len(index.search(_req("ttl", limit=1))) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "", "id": "x", "text": "t"},
        {"kind": "k", "id": "x", "text": ""},
        {"kind": "k", "id": "x", "text": "t", "owner_user_id": ""},
        {"kind": "k", "id": "x", "text": "t", "parent_id": ""},
    ],
)
def test_search_document_rejects_empty_fields(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        SearchDocument(**kwargs)
