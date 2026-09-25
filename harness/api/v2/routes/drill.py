"""`/v2` drill routes: items and ws-scoped answers (#34, #61).

Items are the pack's drill items plus generated documents of resource
``drill``. The learner never gets ``expected``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator

from harness.api.v2.deps import (
    EventIdDep,
    EventTransactionV2Dep,
    GeneratedDocumentsDep,
    PackV2Dep,
    UserIdDep,
    replay_or_conflict,
    ws_or_404,
)
from harness.api.v2.models import Text, V2Model, reject_null
from harness.core.drill import (
    ANSWERED,
    DrillError,
    DrillService,
    generated_items,
    pack_items,
)
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.json_types import PlainJson

router = APIRouter(tags=["drill"])


def drill_service_of(pack: PackV2Dep, generated: GeneratedDocumentsDep) -> DrillService:
    return DrillService([*pack_items(pack), *generated_items(generated.list("drill"))])


DrillServiceDep = Annotated[DrillService, Depends(drill_service_of)]


class AnswerRequest(V2Model):
    # Mirrors the drill.answered payload schema so bad input is a 4xx here,
    # not a contract failure (500) inside tx.append. Omitted, not null (#93).
    actual: Annotated[Text, Field(min_length=1, max_length=20000)] | None = None
    artifact_id: Annotated[Text, Field(pattern=r"^art_[0-9A-Za-z]{1,64}$")] | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_explicit_null(cls, data: Any) -> Any:
        return reject_null(data, "actual", "artifact_id")


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
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    ws_id: str,
    item_id: str,
    body: AnswerRequest,
) -> dict[str, PlainJson]:
    # Replay first (#93): a resend must return the stored event even if the
    # pack changed since (e.g. the item was replaced), before any item lookup
    # or answer-mode validation that could differ on retry.
    existing = tx.get(event_id)
    if existing is not None:
        payload: dict[str, PlainJson] = {
            "item_id": item_id,
            "answer_mode": str(existing.payload.get("answer_mode")),
        }
        if body.actual is not None:
            payload["actual"] = body.actual
        if body.artifact_id is not None:
            payload["artifact_id"] = body.artifact_id
        candidate = EventV2(
            id=event_id,
            type=ANSWERED,
            actor="learner",
            user_id=user_id,
            session_id=existing.session_id,
            ws_id=ws_id,
            payload=payload,
        )
        result = replay_or_conflict(tx, candidate)
        assert result is not None
        return result.to_dict()
    ws = ws_or_404(tx, ws_id, user_id=user_id)
    try:
        event = service.answer(
            tx,
            event_id=event_id,
            user_id=user_id,
            session_id=str(ws["session_id"]),
            ws_id=ws_id,
            item_id=item_id,
            actual=body.actual,
            artifact_id=body.artifact_id,
        )
    except DrillError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return event.to_dict()
