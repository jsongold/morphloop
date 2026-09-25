"""PostgreSQL implementation of the GeneratedDocumentStore Port (#77).

Table ``generated_documents`` from migration ``c4e8a2d6f1b3`` (primary key
``(resource, id)``; UPDATE is rejected by a trigger). A duplicate add is
``INSERT ... ON CONFLICT DO NOTHING`` returning no row, so concurrent adds of
one key store exactly one document.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import Engine, RowMapping

from harness.core.ports.generated_documents import (
    GeneratedDocument,
    GeneratedDocumentExistsError,
    GeneratedDocumentStore,
)
from harness.core.ports.json_types import JsonObject, to_plain_object

_INSERT = text(
    """
    INSERT INTO generated_documents (resource, id, body, labels, provenance)
    VALUES (:resource, :id, CAST(:body AS JSONB), :labels, CAST(:provenance AS JSONB))
    ON CONFLICT (resource, id) DO NOTHING
    RETURNING id
    """
)
_COLUMNS = "resource, id, body, labels, provenance"
_GET = text(f"SELECT {_COLUMNS} FROM generated_documents WHERE resource = :resource AND id = :id")
# COLLATE "C" orders by code point, matching Python's sorted() on str.
_LIST = text(
    f"""
    SELECT {_COLUMNS} FROM generated_documents
    WHERE resource = :resource AND (CAST(:label AS TEXT) IS NULL OR :label = ANY(labels))
    ORDER BY id COLLATE "C"
    """
)


def _to_json(document: JsonObject) -> str:
    # allow_nan=False: NaN / Infinity are not JSON and JSONB rejects them.
    return json.dumps(to_plain_object(document), allow_nan=False, ensure_ascii=False)


def _row_to_document(row: RowMapping) -> GeneratedDocument:
    return GeneratedDocument(
        resource=row["resource"],
        id=row["id"],
        body=row["body"],
        labels=tuple(row["labels"]),
        provenance=row["provenance"],
    )


class PostgresGeneratedDocumentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, document: GeneratedDocument) -> None:
        params = {
            "resource": document.resource,
            "id": document.id,
            "body": _to_json(document.body),
            "labels": list(document.labels),
            "provenance": _to_json(document.provenance),
        }
        with self._engine.begin() as conn:
            if conn.execute(_INSERT, params).one_or_none() is None:
                raise GeneratedDocumentExistsError(document.resource, document.id)

    def get(self, resource: str, id: str) -> GeneratedDocument | None:
        with self._engine.connect() as conn:
            row = conn.execute(_GET, {"resource": resource, "id": id}).mappings().one_or_none()
        return None if row is None else _row_to_document(row)

    def list(self, resource: str, *, label: str | None = None) -> Sequence[GeneratedDocument]:
        with self._engine.connect() as conn:
            rows = conn.execute(_LIST, {"resource": resource, "label": label}).mappings().all()
        return [_row_to_document(row) for row in rows]


if TYPE_CHECKING:

    def _conforms(engine: Engine) -> GeneratedDocumentStore:
        return PostgresGeneratedDocumentStore(engine)
