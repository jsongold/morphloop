"""`/v2/ws/{ws_id}/memo/entries`: an append-only memo log (#34, #59).

`ws_id` is resolved through the `ws` view on the request's transaction
(`ws_or_404`: 404 unless it exists and belongs to the learner); the memo
package itself never imports the ws package (resources refer to each other
by id). There is no edit or delete route: a correction is posted as a new
entry.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path
from pydantic import Field, model_validator

from harness.api.problems import problem
from harness.api.v2.deps import (
    EventIdDep,
    EventTransactionV2Dep,
    PackV2Dep,
    UserIdDep,
    replay_or_conflict,
    ws_or_404,
)
from harness.api.v2.models import Text, V2Model
from harness.api.v2.pagination import CursorQuery, encode_cursor
from harness.core.labels import LabelError
from harness.core.memo.entries import (
    MemoEntries,
    append_memo_entry,
    build_memo_appended,
    entry_for,
)

router = APIRouter(prefix="/ws/{ws_id}/memo", tags=["memo"])

_WsId = Annotated[str, Path(pattern=r"^ws_[0-9A-Za-z]{1,64}$")]
_LABEL_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}(:[a-z0-9][a-z0-9._-]{0,127})?$"
_LabelStr = Annotated[str, Field(pattern=_LABEL_PATTERN)]


class MemoSource(V2Model):
    """What an entry is about: exactly one of a highlight or a chat thread."""

    highlight_id: Annotated[str | None, Field(pattern=r"^hl_[0-9A-Za-z]{1,64}$")] = None
    thread_id: Annotated[str | None, Field(pattern=r"^thr_[0-9A-Za-z]{1,64}$")] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> MemoSource:
        if (self.highlight_id is None) == (self.thread_id is None):
            raise ValueError("source must set exactly one of highlight_id or thread_id")
        return self


class MemoEntryCreate(V2Model):
    """Request body of `POST /ws/{ws_id}/memo/entries`."""

    actor: Literal["learner", "assistant"]
    body: Annotated[Text, Field(min_length=1, max_length=4000)]
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
    # Replay first: a resend takes its session from the stored event, so the
    # ws lookup below only runs for a genuinely new request.
    existing = tx.get(event_id)
    if existing is not None:
        session_id = existing.session_id
    else:
        session_id = str(ws_or_404(tx, ws_id, user_id=user_id)["session_id"])
    source = body.source.model_dump(exclude_none=True) if body.source is not None else None
    candidate = build_memo_appended(
        event_id=event_id,
        user_id=user_id,
        ws_id=ws_id,
        session_id=session_id,
        actor=body.actor,
        body=body.body,
        source=source,
        labels=list(body.labels),
    )
    stored = replay_or_conflict(tx, candidate)
    if stored is not None:
        return entry_for(tx, stored)
    try:
        return append_memo_entry(tx, candidate=candidate, pack=pack)
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
def list_entries(
    ws_id: _WsId, tx: EventTransactionV2Dep, user_id: UserIdDep, page: CursorQuery
) -> dict[str, Any]:
    ws_or_404(tx, ws_id, user_id=user_id)
    entries, next_cursor = MemoEntries.list_for_ws_page(
        tx, ws_id, after=page.after, limit=page.limit
    )
    return {
        "entries": entries,
        "next_cursor": encode_cursor(next_cursor) if next_cursor is not None else None,
    }
