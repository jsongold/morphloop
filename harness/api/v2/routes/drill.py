"""`/v2` drill routes: items and ws-scoped answers (#34, #61).

Items are the pack's drill items plus generated documents of resource
``drill``. The learner never gets ``expected``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, model_validator

from harness.api.backend import harness_version
from harness.api.v2.deps import (
    EventIdDep,
    EventStoreV2Dep,
    GeneratedDocumentsDep,
    PackV2Dep,
    UserIdDep,
    replay_or_conflict,
    ws_or_404,
)
from harness.api.v2.models import Text, V2Model, reject_null
from harness.core.contract_schemas import ContractSchemas
from harness.core.drill import (
    ANSWERED,
    DrillError,
    DrillService,
    generated_items,
    pack_items,
)
from harness.core.drill.judge import (
    GAP_SCHEMA_ID,
    DrillJudgeError,
    claim_judgment,
    compute_gap,
    load_judge_inputs,
    record_judgment,
    save_judge_inputs,
    stored_judgment,
)
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.json_types import PlainJson
from harness.core.ports.llm import LLMProvenance, LLMProvider

router = APIRouter(tags=["drill"])


def drill_service_of(pack: PackV2Dep, generated: GeneratedDocumentsDep) -> DrillService:
    return DrillService(
        [
            *pack_items(pack),
            *generated_items(generated.list("drill"), generated.list("artifact"), pack=pack),
        ]
    )


DrillServiceDep = Annotated[DrillService, Depends(drill_service_of)]


def drill_llm_of(request: Request) -> LLMProvider:
    provider: LLMProvider | None = getattr(request.app.state, "drill_llm", None)
    if provider is None:
        from harness.adapters.litellm import LiteLLMProvider

        provider = LiteLLMProvider()
        request.app.state.drill_llm = provider
    return provider


def drill_judge_config_of(pack: PackV2Dep) -> tuple[LLMProvenance, str] | None:
    role = pack.llm_roles.get("judge")
    if role is None or role.output_schema != GAP_SCHEMA_ID:
        return None
    parameters = {"temperature": role.temperature, "max_tokens": role.max_tokens}
    return (
        LLMProvenance(
            provider="litellm",
            model=role.model,
            prompt_id=role.role,
            prompt_version=pack.pack_version,
            generation_parameters={k: v for k, v in parameters.items() if v is not None},
        ),
        role.prompt_text,
    )


DrillJudgeConfigDep = Annotated[tuple[LLMProvenance, str] | None, Depends(drill_judge_config_of)]


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
    store: EventStoreV2Dep,
    pack: PackV2Dep,
    llm: Annotated[LLMProvider, Depends(drill_llm_of)],
    judge_config: DrillJudgeConfigDep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    ws_id: str,
    item_id: str,
    body: AnswerRequest,
) -> dict[str, PlainJson]:
    # Replay first (#93): a resend must return the stored event even if the
    # pack changed since (e.g. the item was replaced), before any item lookup
    # or answer-mode validation that could differ on retry. The claim (#124)
    # is taken before the transaction so a concurrent retry of the same key
    # never reaches the LLM: it sees the judgment or leaves it pending.
    with claim_judgment(event_id) as claimed:
        with store.transaction() as tx:
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
                event = replay_or_conflict(tx, candidate)
                assert event is not None
                # A pre-rollout answer (or one written by an older worker) has no
                # snapshot view, so backfill it from the current pack instead of
                # pending forever. A pack that no longer has the item stays pending.
                if (
                    stored_judgment(tx, event_id) is None
                    and load_judge_inputs(tx, event_id) is None
                ):
                    try:
                        save_judge_inputs(tx, event, service.get_item(item_id), pack)
                    except DrillError:
                        pass
            else:
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
                # The item as answered, so a retry after a pack change judges the
                # same material (#124).
                save_judge_inputs(tx, event, service.get_item(item_id), pack)
            judged = stored_judgment(tx, event_id) is not None
            inputs = None if judged else load_judge_inputs(tx, event_id)
        if judged:
            return {**event.to_dict(), "judgment_status": "complete"}
        if not claimed or inputs is None:
            return {**event.to_dict(), "judgment_status": "pending"}
        item, judge_pack = inputs
        try:
            if item.answer_mode != "choice" and judge_config is None:
                raise DrillJudgeError("the pack declares no drill gap judge")
            events = (
                store.read(user_id=user_id, ws_id=ws_id) if item.answer_mode == "artifact" else ()
            )
            gap, provenance = compute_gap(
                answer=event,
                item=item,
                pack=judge_pack,
                schemas=ContractSchemas.load(),
                llm=llm if judge_config is not None else None,
                llm_provenance=judge_config[0] if judge_config is not None else None,
                prompt=judge_config[1] if judge_config is not None else None,
                artifact_events=events,
            )
        except DrillJudgeError:
            return {**event.to_dict(), "judgment_status": "pending"}
        with store.transaction() as tx:
            record_judgment(
                tx,
                answer=event,
                gap=gap,
                pack=judge_pack,
                harness_version=harness_version(),
                llm_provenance=provenance,
            )
        return {**event.to_dict(), "judgment_status": "complete"}
