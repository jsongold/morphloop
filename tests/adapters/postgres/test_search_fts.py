"""PostgresSearchIndex: tsvector rank + pg_trgm partial match, owner scope (#180 S3b, #230)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from harness.adapters.postgres.search_fts import PostgresSearchIndex
from harness.core.ports.search import KeywordSearchRequest, SearchDocument


@pytest.fixture
def engine(pg_url: str) -> Engine:
    return create_engine(pg_url)


@pytest.fixture
def index(engine: Engine) -> PostgresSearchIndex:
    return PostgresSearchIndex(engine)


def _req(
    text: str, user_id: str = "u1", limit: int = 10, resources: tuple[str, ...] = ()
) -> KeywordSearchRequest:
    return KeywordSearchRequest(text=text, user_id=user_id, limit=limit, resources=resources)


def _kind(prefix: str) -> str:
    """A unique resource label per test so shared table rows never collide."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_only_shared_and_own_rows_are_returned(index: PostgresSearchIndex) -> None:
    kind = _kind("textbook_block")
    memo_kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(kind=kind, id="b1", parent_id="doc1", text="DNS TTL resolver"),
            SearchDocument(
                kind=memo_kind, id="m1", parent_id="ws1", owner_user_id="u1", text="my ttl note"
            ),
            SearchDocument(
                kind=memo_kind,
                id="m2",
                parent_id="ws2",
                owner_user_id="u2",
                text="their ttl note",
            ),
        ]
    )
    hits = index.search(_req("ttl", resources=(kind, memo_kind)))
    assert {(h.kind, h.id) for h in hits} == {(kind, "b1"), (memo_kind, "m1")}
    other = index.search(_req("ttl", user_id="u2", resources=(kind, memo_kind)))
    assert {h.id for h in other} == {"b1", "m2"}


def test_hits_carry_parent_id_and_keyword_source(index: PostgresSearchIndex) -> None:
    kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(
                kind=kind, id="e1", parent_id="ws1", owner_user_id="u1", text="my ttl note"
            )
        ]
    )
    hit = index.search(_req("ttl", resources=(kind,)))[0]
    assert (hit.parent_id, hit.source) == ("ws1", "keyword")


def test_owner_scope_applies_before_the_limit(index: PostgresSearchIndex) -> None:
    """A low `limit` must not be exhausted by another learner's rows: the owner
    filter is in the WHERE clause, so ranking and LIMIT only ever see u1's rows
    plus shared rows."""
    kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(kind=kind, id=f"other{i}", owner_user_id="u2", text="widget widget")
            for i in range(5)
        ]
        + [SearchDocument(kind=kind, id="mine", owner_user_id="u1", text="widget")]
    )
    hits = index.search(_req("widget", limit=1, resources=(kind,)))
    assert [h.id for h in hits] == ["mine"]


def test_japanese_substring_is_findable_via_pg_trgm(index: PostgresSearchIndex) -> None:
    """`search_tsv` uses the 'simple' config, which does not segment Japanese text
    (no spaces -> one lexeme), so plainto_tsquery alone would only match the whole
    string. This checks the pg_trgm/ILIKE leg actually finds a JA substring."""
    kind = _kind("textbook_block")
    index.upsert(
        [
            SearchDocument(
                kind=kind, id="ja1", text="DNSはドメイン名をIPアドレスに変換する仕組みです"
            )
        ]
    )
    hits = index.search(_req("ドメイン名", resources=(kind,)))
    assert [h.id for h in hits] == ["ja1"]
    assert index.search(_req("存在しない単語列", resources=(kind,))) == []


def test_query_with_percent_is_literal_not_a_wildcard(index: PostgresSearchIndex) -> None:
    kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(kind=kind, id="lit", owner_user_id="u1", text="scored 100% today"),
            SearchDocument(kind=kind, id="other", owner_user_id="u1", text="unrelated widget"),
        ]
    )
    hits = index.search(_req("100%", resources=(kind,)))
    assert [h.id for h in hits] == ["lit"]


def test_query_with_underscore_is_literal_not_a_single_char_wildcard(
    index: PostgresSearchIndex,
) -> None:
    kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(kind=kind, id="lit", owner_user_id="u1", text="zz a_b zz"),
            SearchDocument(kind=kind, id="other", owner_user_id="u1", text="zz axb zz"),
        ]
    )
    hits = index.search(_req("a_b", resources=(kind,)))
    assert [h.id for h in hits] == ["lit"]


def test_query_with_backslash_is_literal(index: PostgresSearchIndex) -> None:
    kind = _kind("memo_entry")
    index.upsert(
        [SearchDocument(kind=kind, id="lit", owner_user_id="u1", text="path is C:\\temp\\file")]
    )
    hits = index.search(_req("C:\\temp", resources=(kind,)))
    assert [h.id for h in hits] == ["lit"]


def test_upsert_is_idempotent_by_kind_and_id(index: PostgresSearchIndex, engine: Engine) -> None:
    kind = _kind("memo_entry")
    index.upsert([SearchDocument(kind=kind, id="e1", owner_user_id="u1", text="first version")])
    index.upsert([SearchDocument(kind=kind, id="e1", owner_user_id="u1", text="second version")])
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT text FROM search_documents WHERE resource = :r AND source_id = 'e1'"),
            {"r": kind},
        ).all()
    assert [r[0] for r in rows] == ["second version"]


def test_delete_removes_the_row(index: PostgresSearchIndex) -> None:
    kind = _kind("memo_entry")
    index.upsert([SearchDocument(kind=kind, id="e1", owner_user_id="u1", text="ttl note")])
    assert index.search(_req("ttl", resources=(kind,)))
    index.delete(kind, "e1")
    assert index.search(_req("ttl", resources=(kind,))) == []


def test_delete_of_missing_row_is_a_no_op(index: PostgresSearchIndex) -> None:
    index.delete(_kind("memo_entry"), "missing")


def test_limit_is_respected(index: PostgresSearchIndex) -> None:
    kind = _kind("memo_entry")
    index.upsert(
        [
            SearchDocument(kind=kind, id=f"e{i}", owner_user_id="u1", text="ttl ttl ttl")
            for i in range(5)
        ]
    )
    assert len(index.search(_req("ttl", limit=2, resources=(kind,)))) == 2
