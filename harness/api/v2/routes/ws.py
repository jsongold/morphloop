"""`/v2` ws routes: workspaces and their threads (#34, #57)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from harness.api.v2.deps import EventIdDep, EventTransactionV2Dep, UserIdDep
from harness.core.ports.events_v2 import EventIdConflictError
from harness.core.ports.json_types import JsonObject, PlainJson
from harness.core.ws import WsError, create_thread, create_ws, get_ws, list_ws

router = APIRouter(tags=["ws"])

# Mirrors the ws.created / thread.created payload schemas so a bad label is a
# 4xx here, not a contract failure (500) inside tx.append.
_LABEL_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}(:[a-z0-9][a-z0-9._-]{0,127})?$"
Label = Annotated[str, Field(pattern=_LABEL_PATTERN)]
SessionId = Annotated[str, Field(pattern=r"^ses_[0-9A-Za-z]{1,64}$")]


class TargetRequest(BaseModel):
    # Only `kind` is this resource's business; a named kind's own fields
    # (doc_id, entry_id, ...) are validated by that resource, not here.
    model_config = ConfigDict(extra="allow")

    kind: Literal["textbook_block", "memo_entry", "drill_item", "artifact"]


class CreateWsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: SessionId
    labels: list[Label] = Field(default_factory=list)


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: TargetRequest | None = None
    labels: list[Label] = Field(default_factory=list)


@router.post("/ws", status_code=201)
def post_ws(
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    body: CreateWsRequest,
) -> dict[str, PlainJson]:
    try:
        event = create_ws(
            tx, event_id=event_id, user_id=user_id, session_id=body.session_id, labels=body.labels
        )
    except EventIdConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return event.to_dict()


@router.get("/ws")
def get_ws_list(
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    session_id: Annotated[str | None, Query()] = None,
) -> JsonObject:
    return {"items": list_ws(tx, user_id=user_id, session_id=session_id)}


@router.get("/ws/{ws_id}")
def get_one_ws(tx: EventTransactionV2Dep, user_id: UserIdDep, ws_id: str) -> JsonObject:
    try:
        return get_ws(tx, ws_id, user_id=user_id)
    except WsError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/ws/{ws_id}/threads", status_code=201)
def post_thread(
    tx: EventTransactionV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    ws_id: str,
    body: CreateThreadRequest | None = None,
) -> dict[str, PlainJson]:
    body = body or CreateThreadRequest()
    target = body.target.model_dump() if body.target is not None else None
    try:
        event = create_thread(
            tx,
            event_id=event_id,
            user_id=user_id,
            ws_id=ws_id,
            target=target,
            labels=body.labels,
        )
    except WsError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except EventIdConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return event.to_dict()
