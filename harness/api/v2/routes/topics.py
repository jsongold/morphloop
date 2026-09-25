"""The loaded pack's learner-visible topics."""

from __future__ import annotations

from fastapi import APIRouter

from harness.api.v2.deps import PackV2Dep
from harness.core.ports import PlainJson
from harness.core.session.service import learner_topic_tree

router = APIRouter(tags=["session"])


@router.get("/topics")
def list_topics(pack: PackV2Dep) -> dict[str, PlainJson]:
    return learner_topic_tree(pack)
