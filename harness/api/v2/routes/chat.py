"""`/v2/ws/{ws_id}/threads/{thread_id}/messages` (chat resource, #63).

Wiring: the tool-calling LLM comes from `harness.cli.wiring.llm_provider` (the
litellm adapter, or the deterministic fake under `MORPHLOOP_LLM_PROVIDER=fake`,
#130) unless a test overrides `chat_llm_of`; the model, generation parameters
and prompt are the pack's `assistant` LLM role (`PackV2Dep`, ADR-0002).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from harness.api.problems import problem
from harness.api.v2.deps import EventIdDep, EventStoreV2Dep, PackV2Dep, UserIdDep
from harness.api.v2.models import Text, V2Model
from harness.api.v2.pagination import CursorQuery, encode_cursor
from harness.core.chat import (
    AssistantConfig,
    AssistantError,
    ThreadNotFoundError,
    send_message,
)
from harness.core.chat.service import list_messages_page
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMError, LLMProvenance, LLMToolProvider

router = APIRouter(tags=["chat"])
PATH = "/ws/{ws_id}/threads/{thread_id}/messages"
ASSISTANT_ROLE = "assistant"
# ponytail: a safety bound, not tuning; move into the pack's llm role once it has a field.
MAX_TOOL_ROUNDS = 4


def chat_llm_of(request: Request) -> LLMToolProvider:
    provider: LLMToolProvider | None = getattr(request.app.state, "chat_llm", None)
    if provider is None:
        from harness.cli import wiring  # heavy import, only when used

        provider = wiring.llm_provider()
        request.app.state.chat_llm = provider
    return provider


def chat_config_of(pack: PackV2Dep) -> AssistantConfig:
    role = pack.llm_roles.get(ASSISTANT_ROLE)
    if role is None:
        raise HTTPException(500, f"the pack declares no {ASSISTANT_ROLE!r} llm role")
    parameters = {"temperature": role.temperature, "max_tokens": role.max_tokens}
    return AssistantConfig(
        llm=LLMProvenance(
            provider="litellm",  # the adapter echoes the provider it actually routed to
            model=role.model,
            prompt_id=role.role,
            prompt_version=pack.pack_version,
            generation_parameters={k: v for k, v in parameters.items() if v is not None},
        ),
        system_prompt=role.prompt_text,
        max_tool_rounds=MAX_TOOL_ROUNDS,
    )


class SendBody(V2Model):
    text: Annotated[Text, Field(min_length=1)]
    allow_writes: bool = False


def _not_found(error: ThreadNotFoundError) -> JSONResponse:
    return problem(status=404, code="not-found", detail=str(error))


@router.get(PATH, response_model=None)
def get_messages(
    ws_id: str, thread_id: str, store: EventStoreV2Dep, user_id: UserIdDep, page: CursorQuery
) -> JsonObject | JSONResponse:
    try:
        messages, next_cursor = list_messages_page(
            store, user_id, ws_id, thread_id, after=page.after, limit=page.limit
        )
    except ThreadNotFoundError as error:
        return _not_found(error)
    return {
        "messages": list(messages),
        "next_cursor": encode_cursor(next_cursor) if next_cursor else None,
    }


@router.post(PATH, response_model=None)
def post_message(
    ws_id: str,
    thread_id: str,
    body: SendBody,
    store: EventStoreV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    llm: Annotated[LLMToolProvider, Depends(chat_llm_of)],
    config: Annotated[AssistantConfig, Depends(chat_config_of)],
) -> dict[str, JsonObject] | JSONResponse:
    try:
        result = send_message(
            store,
            llm,
            config,
            user_id=user_id,
            ws_id=ws_id,
            thread_id=thread_id,
            event_id=event_id,
            text=body.text,
            allow_writes=body.allow_writes,
        )
    except ThreadNotFoundError as error:
        return _not_found(error)
    except (LLMError, AssistantError) as error:
        return problem(status=502, code="llm-failed", detail=str(error))
    return {"sent": result.sent, "reply": result.reply}
