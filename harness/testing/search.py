"""In-memory :class:`~harness.core.ports.search.SearchIndex` (one process only).

Also a naive :class:`~harness.core.ports.search.KeywordSearchBackend` over what
was indexed (case-insensitive substring, score = occurrence count), so tests can
check the learner scope end to end without Postgres.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

from harness.core.ports.search import (
    KeywordSearchBackend,
    KeywordSearchRequest,
    SearchDocument,
    SearchHit,
    SearchIndex,
)


class InMemorySearchIndex:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], SearchDocument] = {}
        self._lock = threading.Lock()

    def upsert(self, documents: Sequence[SearchDocument]) -> None:
        with self._lock:
            for doc in documents:
                self.documents[(doc.kind, doc.id)] = doc

    def delete(self, kind: str, id: str) -> None:
        with self._lock:
            self.documents.pop((kind, id), None)

    def search(self, request: KeywordSearchRequest) -> Sequence[SearchHit]:
        needle = request.text.casefold()
        with self._lock:
            docs = list(self.documents.values())
        hits = [
            SearchHit(
                kind=d.kind,
                id=d.id,
                parent_id=d.parent_id,
                score=float(d.text.casefold().count(needle)),
                source="keyword",
            )
            for d in docs
            if d.owner_user_id in (None, request.user_id)
            and (not request.resources or d.kind in request.resources)
            and needle in d.text.casefold()
        ]
        hits.sort(key=lambda h: (-h.score, h.kind, h.id))
        return hits[: request.limit]


if TYPE_CHECKING:

    def _conforms_index() -> SearchIndex:
        return InMemorySearchIndex()

    def _conforms_backend() -> KeywordSearchBackend:
        return InMemorySearchIndex()
