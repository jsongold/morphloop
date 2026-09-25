"""HTTP routes for the highlight resource (ADR-0018, issue #34/#60).

``POST /v2/ws/{ws_id}/highlights`` -> ``highlight.created``.
``DELETE /v2/ws/{ws_id}/highlights/{highlight_id}`` -> ``highlight.removed``.
``GET /v2/ws/{ws_id}/highlights`` -> ``HighlightView`` (current highlights only).

Request bodies mirror the payload contract's shape constraints (length,
pattern, required fields) in the pydantic model, so a malformed request is a
422 and never reaches ``tx.append`` (common rule, issue #34). The one
constraint JSON Schema cannot express -- a ``TextPositionSelector``'s
``start < end`` -- is checked by :mod:`harness.core.highlight.anchor` from a
pydantic validator, so it is also a 422 raised here, not a stored event that
silently violates the invariant. Everything else (labels vocabulary, the
event append, the view read) is :mod:`harness.core.highlight.service`; this
module does request/response shape and dependency wiring only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.api.problems import problem
from harness.api.v2.deps import EventIdDep, EventTransactionV2Dep, PackV2Dep, UserIdDep
from harness.core.highlight.anchor import check_text_position
from harness.core.highlight.service import (
    HighlightNotFoundError,
    create_highlight,
    list_highlights,
    remove_highlight,
)
from harness.core.labels import LabelError
from harness.core.ports.json_types import JsonObject, JsonValue

router = APIRouter(prefix="/ws/{ws_id}/highlights", tags=["highlight"])

# Mirror the shape constraints of contracts/schemas/ so a bad request is a 4xx,
# never a ContractValidationError (500) inside tx.append.
_WS_ID_PATTERN = r"^ws_[0-9A-Za-z]{1,64}$"  # common/ids.json#/$defs/ws_id
_HIGHLIGHT_ID_PATTERN = r"^hl_[0-9A-Za-z]{1,64}$"  # common/ids.json#/$defs/highlight_id
_DEFINITION_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"  # common/ids.json#/$defs/definition_id

WsIdPath = Annotated[str, Path(pattern=_WS_ID_PATTERN)]
HighlightIdPath = Annotated[str, Path(pattern=_HIGHLIGHT_ID_PATTERN)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextQuoteSelectorIn(_Body):
    """``components/highlight.yaml#/TextQuoteSelector``."""

    type: Literal["TextQuoteSelector"]
    exact: str = Field(min_length=1)
    prefix: str | None = None
    suffix: str | None = None


class TextPositionSelectorIn(_Body):
    """``components/highlight.yaml#/TextPositionSelector``."""

    type: Literal["TextPositionSelector"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class AnchorIn(_Body):
    """``components/highlight.yaml#/HighlightAnchor``. v0.2.0: one block only."""

    doc_id: str = Field(pattern=_DEFINITION_ID_PATTERN)
    block_id: str = Field(pattern=_DEFINITION_ID_PATTERN)
    selector: tuple[TextQuoteSelectorIn, TextPositionSelectorIn]

    @model_validator(mode="after")
    def _start_before_end(self) -> AnchorIn:
        _, position = self.selector
        check_text_position(position.start, position.end)  # raises -> 422 (ValueError)
        return self

    def to_json(self) -> JsonObject:
        quote, position = self.selector
        anchor: dict[str, JsonValue] = {
            "doc_id": self.doc_id,
            "block_id": self.block_id,
            "selector": [quote.model_dump(exclude_none=True), position.model_dump()],
        }
        return anchor


class HighlightCreateRequest(_Body):
    """``components/highlight.yaml#/HighlightCreateRequest``."""

    anchor: AnchorIn
    labels: list[str] = Field(default_factory=list)


def _label_problem(exc: LabelError) -> JSONResponse:
    return problem(
        status=422,
        code="validation-failed",
        detail=str(exc),
        errors=[
            {"path": "$.labels", "message": f"{label}: {reason}"}
            for label, reason in exc.offending.items()
        ],
    )


@router.post("", status_code=201, response_model=None)
def create(
    ws_id: WsIdPath,
    body: HighlightCreateRequest,
    tx: EventTransactionV2Dep,
    event_id: EventIdDep,
    user_id: UserIdDep,
    pack: PackV2Dep,
) -> Response:
    try:
        doc = create_highlight(
            tx,
            event_id=event_id,
            user_id=user_id,
            ws_id=ws_id,
            anchor=body.anchor.to_json(),
            labels=body.labels,
            label_vocabulary=pack.labels,
            topic_ids=pack.topic_ids,
        )
    except LabelError as exc:
        return _label_problem(exc)
    return JSONResponse(dict(doc), status_code=201)


@router.get("", response_model=None)
def list_(ws_id: WsIdPath, tx: EventTransactionV2Dep) -> dict[str, list[JsonObject]]:
    return {"highlights": list_highlights(tx, ws_id)}


@router.delete("/{highlight_id}", status_code=204, response_model=None)
def remove(
    ws_id: WsIdPath,
    highlight_id: HighlightIdPath,
    tx: EventTransactionV2Dep,
    event_id: EventIdDep,
    user_id: UserIdDep,
) -> Response:
    try:
        remove_highlight(
            tx, event_id=event_id, user_id=user_id, ws_id=ws_id, highlight_id=highlight_id
        )
    except HighlightNotFoundError:
        return problem(
            status=404,
            code="not-found",
            detail=f"no active highlight {highlight_id!r} in ws {ws_id!r}",
        )
    return Response(status_code=204)
