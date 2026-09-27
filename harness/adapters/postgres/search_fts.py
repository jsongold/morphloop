"""PostgreSQL FTS implementation of the SearchIndex + KeywordSearchBackend Ports (#180 S3b).

Table ``search_documents`` (migrations ``d7b1e3f5a9c2``, ``c9e1f3a5b7d0``): primary key
``(resource, source_id)``, a generated ``search_tsv`` column (``to_tsvector('simple', text)``,
GIN-indexed) and a ``pg_trgm`` GIN index on ``text``.

Two legs feed the ranking, ``GREATEST`` of the two:

- ``ts_rank`` over ``search_tsv @@ plainto_tsquery('simple', text)`` -- exact/prefix lexeme
  matches, whitespace-tokenized.
- ``similarity(text, query)`` (``pg_trgm``) -- trigram overlap, scored regardless of whether
  the tsquery matched.

The ``'simple'`` config has no stemmer or stopwords and does not segment CJK text: Japanese
input with no spaces becomes one long lexeme, so ``plainto_tsquery`` alone would only match a
query equal to the whole string. The ``WHERE`` clause's second arm, ``text ILIKE '%'||query||'%'``,
is the partial-match path that actually finds Japanese substrings; the same ``pg_trgm`` GIN
index accelerates it (trigram indexes serve ``LIKE``/``ILIKE``, not only the ``%`` operator). The
query has its backslashes, then its ``%``/``_``, escaped before that arm only (``ESCAPE`` with a
backslash) -- so a learner searching for a literal ``%``/``_`` gets a literal substring match, not
a wildcard; the tsquery/similarity arms take the raw query, since neither treats those characters
specially.
Both legs are library-free: no tokenizer dependency needed for v0.4 (fugashi/janome are S3c's
concern, tokenizing before indexing, not this query).

Per-user scoping (``owner_user_id IS NULL OR owner_user_id = :user_id``) is in the ``WHERE``
clause, so it is applied before ``ORDER BY`` ranks the remaining rows -- another learner's rows
never affect score or the ``LIMIT``. Only ``resource``/``source_id``/``parent_id`` are selected;
``text`` is never returned (:class:`~harness.core.ports.search.SearchHit` has no body field).

Lives beside ``claims.py`` and reuses its ``Engine`` the same way (no adapter-local pool or
session). Issue #230 also re-exports ``SearchDocument``/``SearchIndex`` from
``harness.core.ports`` -- done in ``harness/core/ports/__init__.py``, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from harness.core.ports.search import (
    KeywordSearchBackend,
    KeywordSearchRequest,
    SearchBackendError,
    SearchDocument,
    SearchHit,
    SearchIndex,
)

_UPSERT = text(
    """
    INSERT INTO search_documents (resource, source_id, text, owner_user_id, parent_id)
    VALUES (:resource, :source_id, :text, :owner_user_id, :parent_id)
    ON CONFLICT (resource, source_id) DO UPDATE SET
        text = EXCLUDED.text,
        owner_user_id = EXCLUDED.owner_user_id,
        parent_id = EXCLUDED.parent_id,
        updated_at = clock_timestamp()
    """
)
_DELETE = text("DELETE FROM search_documents WHERE resource = :resource AND source_id = :source_id")

_SEARCH_SQL = """
    SELECT resource, source_id, parent_id,
           GREATEST(
               ts_rank(search_tsv, plainto_tsquery('simple', :q)),
               similarity(text, :q)
           ) AS score
    FROM search_documents
    WHERE (owner_user_id IS NULL OR owner_user_id = :user_id)
      AND (
          search_tsv @@ plainto_tsquery('simple', :q)
          OR text ILIKE '%' || :q_like || '%' ESCAPE '\\'
      )
      {resource_clause}
    ORDER BY score DESC, resource, source_id
    LIMIT :limit
"""
_SEARCH = text(_SEARCH_SQL.format(resource_clause=""))
_SEARCH_BY_RESOURCE = text(
    _SEARCH_SQL.format(resource_clause="AND resource IN :resources")
).bindparams(bindparam("resources", expanding=True))


def _escape_like(query: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so an ILIKE pattern treats ``query`` literally."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PostgresSearchIndex:
    """``SearchIndex`` write side + ``KeywordSearchBackend`` query side, one Engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, documents: Sequence[SearchDocument]) -> None:
        if not documents:
            return
        rows = [
            {
                "resource": d.kind,
                "source_id": d.id,
                "text": d.text,
                "owner_user_id": d.owner_user_id,
                "parent_id": d.parent_id,
            }
            for d in documents
        ]
        try:
            with self._engine.begin() as conn:
                conn.execute(_UPSERT, rows)
        except Exception as exc:
            raise SearchBackendError(f"search_documents upsert failed: {exc}") from exc

    def delete(self, kind: str, id: str) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(_DELETE, {"resource": kind, "source_id": id})
        except Exception as exc:
            raise SearchBackendError(f"search_documents delete failed: {exc}") from exc

    def search(self, request: KeywordSearchRequest) -> Sequence[SearchHit]:
        params: dict[str, object] = {
            "q": request.text,
            "q_like": _escape_like(request.text),
            "user_id": request.user_id,
            "limit": request.limit,
        }
        stmt = _SEARCH
        if request.resources:
            stmt = _SEARCH_BY_RESOURCE
            params["resources"] = list(request.resources)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(stmt, params).all()
        except Exception as exc:
            raise SearchBackendError(f"search_documents query failed: {exc}") from exc
        return [
            SearchHit(
                kind=row.resource,
                id=row.source_id,
                parent_id=row.parent_id,
                score=float(row.score),
                source="keyword",
            )
            for row in rows
        ]


if TYPE_CHECKING:

    def _conforms_index(engine: Engine) -> SearchIndex:
        return PostgresSearchIndex(engine)

    def _conforms_backend(engine: Engine) -> KeywordSearchBackend:
        return PostgresSearchIndex(engine)
