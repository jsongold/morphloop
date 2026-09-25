"""`/v2` drill routes: items and ws-scoped answers (#34, #61).

Items are the pack's drill items plus generated documents of resource
``drill``. The learner never gets ``expected``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from harness.api.v2.deps import (
    EventIdDep,
    EventStoreV2Dep,
    EventTransactionV2Dep,
    GeneratedDocumentsDep,
    PackV2Dep,
    UserIdDep,
)
from harness.core.drill import (
    DrillError,
    DrillService,
    generated_items,
    pack_items,
    ws_session_id,
)
from harness.core.ports.events_v2 import EventIdConflictError
from harness.core.ports.json_types import PlainJson

router = APIRouter(tags=["drill"])


def drill_service_of(pack: PackV2Dep, generated: GeneratedDocumentsDep) -> DrillService:
    return DrillService([*pack_items(pack), *generated_items(generated.list("drill"))])


DrillServiceDep = Annotated[DrillService, Depends(drill_service_of)]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Mirrors the drill.answered payload schema so bad input is a 4xx here,
    # not a contract failure (500) inside tx.append.
    actual: Annotated[str, Field(min_length=1, max_length=20000)] | None = None
    artifact_id: Annotated[str, Field(pattern=r"^art_[0-9A-Za-z]{1,64}$")] | None = None


@router.get("/drills")
def list_drills(
    service: DrillServiceDep, labels: Annotated[list[str] | None, Query()] = None
) -> dict[str, PlainJson]:
    return {"items": [item.for_learner() for item in service.list_items(labels or ())]}


@router.get("/drills/{item_id}")
def get_drill(service: DrillServiceDep, item_id: str) -> dict[str, PlainJson]:
    try:
        return service.get_item(item_id).for_learner()
    except DrillError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/ws/{ws_id}/drills/{item_id}/answers", status_code=201)
def answer_drill(
    service: DrillServiceDep,
    store: EventStoreV2Dep,
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    ws_id: str,
    item_id: str,
    body: AnswerRequest,
) -> dict[str, PlainJson]:
    try:
        # ponytail: scans the ws's events for ws.created; read a ws view once #57 has one
        session_id = ws_session_id(ws_id, store.read(ws_id=ws_id))
        event = service.answer(
            tx,
            event_id=event_id,
            user_id=user_id,
            session_id=session_id,
            ws_id=ws_id,
            item_id=item_id,
            actual=body.actual,
            artifact_id=body.artifact_id,
        )
    except DrillError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except EventIdConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return event.to_dict()
