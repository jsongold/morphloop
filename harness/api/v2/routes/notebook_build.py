"""Create a workspace with relevant content."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator

from harness.api.problems import problem
from harness.api.v2.deps import (
    EventIdDep,
    EventTransactionV2Dep,
    GeneratedDocumentsDep,
    PackV2Dep,
    UserIdDep,
)
from harness.api.v2.models import Text, V2Model
from harness.core.labels import LabelError
from harness.core.notebook.build import build_workspace
from harness.core.session.service import PackMismatchError

router = APIRouter(tags=["notebook"])


class BuildBody(V2Model):
    session_id: Annotated[Text, Field(pattern=r"^ses_[0-9A-Za-z]{1,64}$")]
    topic: Text | None = None
    labels: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def _selector(self) -> BuildBody:
        present = self.model_fields_set & {"topic", "labels"}
        if (
            len(present) != 1
            or ("topic" in present and self.topic is None)
            or ("labels" in present and not self.labels)
        ):
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
) -> Any:
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
    except PackMismatchError as exc:
        return problem(status=409, code="state-conflict", detail=str(exc))
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
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        return problem(status=422, code="validation-failed", detail=str(exc))
