"""In-memory :class:`~harness.core.ports.generated_documents.GeneratedDocumentStore`.

Same contract as the Postgres adapter
(``tests/adapters/postgres/test_generated_documents.py`` runs one suite against both).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

from harness.core.ports.generated_documents import (
    GeneratedDocument,
    GeneratedDocumentExistsError,
    GeneratedDocumentStore,
)


class InMemoryGeneratedDocumentStore:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], GeneratedDocument] = {}
        self._lock = threading.Lock()

    def add(self, document: GeneratedDocument) -> None:
        key = (document.resource, document.id)
        with self._lock:
            if key in self._docs:
                raise GeneratedDocumentExistsError(*key)
            self._docs[key] = document

    def get(self, resource: str, id: str) -> GeneratedDocument | None:
        return self._docs.get((resource, id))

    def list(self, resource: str, *, label: str | None = None) -> Sequence[GeneratedDocument]:
        with self._lock:
            docs = [d for (r, _), d in self._docs.items() if r == resource]
        return sorted((d for d in docs if label is None or label in d.labels), key=lambda d: d.id)


if TYPE_CHECKING:

    def _conforms() -> GeneratedDocumentStore:
        return InMemoryGeneratedDocumentStore()
