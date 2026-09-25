"""`/v2/textbook`: a topic's reading list and one doc (#34, #55)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from harness.api.v2.deps import GeneratedDocumentsDep, PackV2Dep
from harness.api.v2.models import Text
from harness.core.ports import PlainJson
from harness.core.textbook.service import Textbook, TextbookNotFoundError

router = APIRouter(prefix="/textbook", tags=["textbook"])


@router.get("/docs")
def list_docs(
    pack: PackV2Dep,
    generated: GeneratedDocumentsDep,
    topic_id: Annotated[Text, Query(min_length=1)],
) -> dict[str, PlainJson]:
    try:
        return {"docs": list(Textbook(pack, generated).reading_list(topic_id))}
    except TextbookNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/docs/{doc_id}")
def get_doc(pack: PackV2Dep, generated: GeneratedDocumentsDep, doc_id: str) -> dict[str, PlainJson]:
    try:
        return Textbook(pack, generated).doc(doc_id)
    except TextbookNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
