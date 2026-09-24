"""Generated documents Port: runtime-generated content of every resource (#34, #77).

Drill items, textbook docs and other content generated at runtime are stored
as JSON documents addressed by ``(resource, id)`` (table ``generated_documents``,
migration ``c4e8a2d6f1b3``). Finalized content is immutable (ADR-0014): a
document is added once and never replaced; adding the same ``(resource, id)``
again raises :class:`GeneratedDocumentExistsError`.

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from harness.core.ports.json_types import JsonObject


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedDocument:
    """One generated document; ``labels`` is kept in the given order."""

    resource: str
    id: str
    body: JsonObject
    labels: tuple[str, ...] = ()
    provenance: JsonObject


class GeneratedDocumentsError(Exception):
    """Base class for generated documents store failures."""


class GeneratedDocumentExistsError(GeneratedDocumentsError):
    """A document is already stored at this ``(resource, id)``."""

    def __init__(self, resource: str, id: str) -> None:
        super().__init__(f"generated document ({resource!r}, {id!r}) already exists")
        self.resource = resource
        self.id = id


class GeneratedDocumentStore(Protocol):
    """Add-only store of generated documents."""

    def add(self, document: GeneratedDocument) -> None:
        """Store ``document``; raise :class:`GeneratedDocumentExistsError` if its
        ``(resource, id)`` is already stored (race-safe)."""
        ...

    def get(self, resource: str, id: str) -> GeneratedDocument | None:
        """Return the document at ``(resource, id)``, or ``None``."""
        ...

    def list(self, resource: str, *, label: str | None = None) -> Sequence[GeneratedDocument]:
        """Documents of ``resource`` (only those carrying ``label`` if given), sorted by id."""
        ...
