"""The v0.4 read paths use an index, not a Seq Scan (#170, #180 S2).

``enable_seqscan = off`` makes the planner pick any usable index even on a
tiny table, so a Seq Scan in the plan means no index can serve the query;
each query must use the index named next to it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from harness.core.settings import Settings

_VECTOR = "[" + ",".join(["0.1"] * Settings().morphloop_embedding_dims) + "]"

_QUERIES = {
    "view keyset": (
        'SELECT key FROM view_documents_v2 WHERE view = :v AND key COLLATE "C" > :k '
        'ORDER BY key COLLATE "C" LIMIT 50',
        {"v": "notes", "k": "a"},
        "view_documents_v2_view_key_c_idx",
    ),
    # A prefix read (drill/highlight/memo/ws) is a bounded range, not
    # starts_with(): that function is not sargable, so it could not use this
    # index (#173, #190 P2 review).
    "view prefix range": (
        "SELECT key FROM view_documents_v2 WHERE view = :v "
        'AND key COLLATE "C" >= :lo AND key COLLATE "C" < :hi ORDER BY key COLLATE "C"',
        {"v": "notes", "lo": "ws_1/", "hi": "ws_1/￾"},
        "view_documents_v2_view_key_c_idx",
    ),
    "events by ws_id": (
        "SELECT position FROM events_v2 WHERE ws_id = :w AND position > :p ORDER BY position",
        {"w": "ws_1", "p": 0},
        "events_v2_ws_id_position_idx",
    ),
    "events by user_id": (
        "SELECT position FROM events_v2 WHERE user_id = :u AND position > :p ORDER BY position",
        {"u": "usr_1", "p": 0},
        "events_v2_user_id_position_idx",
    ),
    "keyword tsvector": (
        "SELECT source_id FROM search_documents WHERE search_tsv @@ plainto_tsquery('simple', :q)",
        {"q": "dns"},
        "search_documents_tsv_idx",
    ),
    "keyword trigram": (
        "SELECT source_id FROM search_documents WHERE text ILIKE :q",
        {"q": "%名前解決%"},
        "search_documents_text_trgm_idx",
    ),
    "semantic hnsw": (
        "SELECT source_id FROM search_embeddings "
        "ORDER BY embedding <=> CAST(:e AS vector) LIMIT 10",
        {"e": _VECTOR},
        "search_embeddings_hnsw_idx",
    ),
}


@pytest.mark.parametrize("name", list(_QUERIES))
def test_query_uses_an_index(pg_engine: Engine, name: str) -> None:
    sql, params, index = _QUERIES[name]
    with pg_engine.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(conn.execute(text(f"EXPLAIN {sql}"), params).scalars())

    assert "Seq Scan" not in plan, plan
    assert index in plan, plan
