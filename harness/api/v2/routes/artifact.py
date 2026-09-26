"""Learner-safe artifact specs from the loaded v2 pack."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from harness.api.v2.deps import PackV2Dep
from harness.core.artifact import ArtifactSpecNotFoundError, learner_artifact_spec
from harness.core.ports.json_types import PlainJson

router = APIRouter(prefix="/artifact-specs", tags=["artifact"])


@router.get("/{spec_id}")
def get_artifact_spec(request: Request, pack: PackV2Dep, spec_id: str) -> dict[str, PlainJson]:
    try:
        return learner_artifact_spec(pack, spec_id, request.app.state.artifact_types)
    except ArtifactSpecNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
