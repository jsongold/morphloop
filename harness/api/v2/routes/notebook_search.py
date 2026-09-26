"""Notebook search endpoint."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from harness.api.v2.deps import EventTransactionV2Dep, GeneratedDocumentsDep, PackV2Dep, UserIdDep
from harness.api.v2.models import Text
from harness.core.notebook.search import search

router = APIRouter(tags=["notebook"])


@router.get("/notebook/search")
def search_notebook(
    q: Annotated[Text, Query(min_length=1)],
    pack: PackV2Dep,
    generated: GeneratedDocumentsDep,
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    session_id: Annotated[Text | None, Query()] = None,
) -> dict[str, Any]:
    return {
        "results": search(
            q, pack=pack, generated=generated, tx=tx, user_id=user_id, session_id=session_id
        )
    }
