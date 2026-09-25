"""Create a workspace with relevant content."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator

from harness.api.v2.deps import (
    EventIdDep,
    EventTransactionV2Dep,
    GeneratedDocumentsDep,
    PackV2Dep,
    UserIdDep,
)
from harness.api.v2.models import Text, V2Model
from harness.core.notebook.build import build_workspace

router = APIRouter(tags=["notebook"])


class BuildBody(V2Model):
    session_id: Annotated[Text, Field(pattern=r"^ses_[0-9A-Za-z]{1,64}$")]
    topic: Text | None = None
    labels: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def _selector(self) -> BuildBody:
        if (self.topic is None) == (not self.labels):
            raise ValueError("provide exactly one of topic or labels")
        return self


@router.post("/notebook/workspace/build", status_code=201)
def build_notebook_workspace(
    body: BuildBody,
    tx: EventTransactionV2Dep,
    event_id: EventIdDep,
    user_id: UserIdDep,
    pack: PackV2Dep,
    generated: GeneratedDocumentsDep,
) -> dict[str, Any]:
    try:
        return build_workspace(
            tx,
            pack=pack,
            generated=generated,
            event_id=event_id,
            user_id=user_id,
            session_id=body.session_id,
            topic=body.topic,
            labels=body.labels,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
