"""`/v2/ws/{ws_id}/memo/entries`: an append-only memo log (#34, #59).

`ws_id` is an opaque path id, never verified against the ws resource (the
wave 6 common rule: resources refer to each other by id, not by importing one
another's package or store). There is no edit or delete route: a correction
is posted as a new entry.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field, model_validator

from harness.api.problems import problem
from harness.api.v2.deps import EventIdDep, EventTransactionV2Dep, PackV2Dep, UserIdDep
from harness.core.labels import LabelError
from harness.core.memo.entries import MemoEntries, append_memo_entry

router = APIRouter(prefix="/ws/{ws_id}/memo", tags=["memo"])

_WsId = Annotated[str, Path(pattern=r"^ws_[0-9A-Za-z]{1,64}$")]
_LABEL_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}(:[a-z0-9][a-z0-9._-]{0,127})?$"
_LabelStr = Annotated[str, Field(pattern=_LABEL_PATTERN)]


class MemoSource(BaseModel):
    """What an entry is about: exactly one of a highlight or a chat thread."""

    highlight_id: Annotated[str | None, Field(pattern=r"^hl_[0-9A-Za-z]{1,64}$")] = None
    thread_id: Annotated[str | None, Field(pattern=r"^thr_[0-9A-Za-z]{1,64}$")] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> MemoSource:
        if (self.highlight_id is None) == (self.thread_id is None):
            raise ValueError("source must set exactly one of highlight_id or thread_id")
        return self


class MemoEntryCreate(BaseModel):
    """Request body of `POST /ws/{ws_id}/memo/entries`."""

    actor: Literal["learner", "assistant"]
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    source: MemoSource | None = None
    labels: Annotated[list[_LabelStr], Field(default_factory=list)]


@router.post("/entries", status_code=201)
def append_entry(
    ws_id: _WsId,
    body: MemoEntryCreate,
    event_id: EventIdDep,
    user_id: UserIdDep,
    pack: PackV2Dep,
    tx: EventTransactionV2Dep,
) -> Any:
    source = body.source.model_dump(exclude_none=True) if body.source is not None else None
    try:
        return append_memo_entry(
            tx,
            event_id=event_id,
            user_id=user_id,
            ws_id=ws_id,
            actor=body.actor,
            body=body.body,
            source=source,
            labels=list(body.labels),
            pack=pack,
        )
    except LabelError as exc:
        return problem(
            status=422,
            code="validation-failed",
            detail=str(exc),
            errors=[
                {"path": "$.labels", "message": f"{label}: {reason}"}
                for label, reason in exc.offending.items()
            ],
        )


@router.get("/entries")
def list_entries(ws_id: _WsId, tx: EventTransactionV2Dep) -> dict[str, Any]:
    return {"entries": MemoEntries.list_for_ws(tx, ws_id)}
