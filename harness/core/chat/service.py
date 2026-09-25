"""Chat: send a message, get the Assistant's reply, list a thread (#63).

Threads belong to the ws resource: they are found by reading the
``thread.created`` events of the ws (opaque ids; no import of that resource).
``chat.sent`` commits before the LLM runs and ``chat.replied`` commits after,
so a long LLM call never holds a transaction and a failed call still leaves
the learner's message as evidence. The reply's event id is derived from the
sent event's id: resending the same ``chat.sent`` id returns the stored reply,
or retries the LLM if none was recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from harness.core.chat.assistant import AssistantConfig, reply
from harness.core.chat.tools import ToolContext
from harness.core.chat.view import REPLIED, SENT, ChatMessagesView
from harness.core.ports.events_v2 import EventStoreV2, EventV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMToolProvider
from harness.core.view import dispatch

THREAD_CREATED = "thread.created"


class ThreadNotFoundError(LookupError):
    """No ``thread.created`` for this thread id in this ws."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SendResult:
    sent: JsonObject
    reply: JsonObject


def find_thread(store: EventStoreV2, user_id: str, ws_id: str, thread_id: str) -> ToolContext:
    """The thread as a tool context (its user, session, target and labels)."""
    # ponytail: scans the ws log; read a ws thread view once one exists.
    for event in store.read(user_id=user_id, ws_id=ws_id):
        if event.type == THREAD_CREATED and event.payload.get("thread_id") == thread_id:
            target = event.payload.get("target")
            labels = event.payload.get("labels")
            return ToolContext(
                store=store,
                user_id=user_id,
                session_id=event.session_id,
                ws_id=ws_id,
                thread_id=thread_id,
                target=target if isinstance(target, dict) else None,
                labels=tuple(str(x) for x in labels) if isinstance(labels, list) else (),
            )
    raise ThreadNotFoundError(f"no thread {thread_id!r} in ws {ws_id!r}")


def _messages(store: EventStoreV2, ws_id: str, thread_id: str) -> list[JsonObject]:
    with store.transaction() as tx:
        return list(ChatMessagesView.messages(tx, ws_id, thread_id))


def list_messages(
    store: EventStoreV2, user_id: str, ws_id: str, thread_id: str
) -> Sequence[JsonObject]:
    """The thread's messages; raises :class:`ThreadNotFoundError`."""
    find_thread(store, user_id, ws_id, thread_id)
    return _messages(store, ws_id, thread_id)


def _append(
    store: EventStoreV2, ctx: ToolContext, event_id: str, type_: str, payload: JsonObject
) -> None:
    event = EventV2(
        id=event_id,
        type=type_,
        actor="learner" if type_ == SENT else "assistant",
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        ws_id=ctx.ws_id,
        payload=payload,
    )
    with store.transaction() as tx:
        result = tx.append(event)
        if result.created:
            dispatch(result.event, tx)


def _message_id(event_id: str) -> str:
    return "msg_" + event_id.replace("-", "")


def send_message(
    store: EventStoreV2,
    provider: LLMToolProvider,
    config: AssistantConfig,
    *,
    user_id: str,
    ws_id: str,
    thread_id: str,
    event_id: str,
    text: str,
    allow_writes: bool,
) -> SendResult:
    """Record the learner's message, run the Assistant, record its reply.

    Raises :class:`ThreadNotFoundError`, ``EventIdConflictError`` (same id,
    other content), ``LLMError`` / ``AssistantError`` (no reply recorded).
    """
    ctx = find_thread(store, user_id, ws_id, thread_id)
    sent_id = _message_id(event_id)
    sent_payload: JsonObject = {
        "thread_id": thread_id,
        "message_id": sent_id,
        "text": text,
        "allow_writes": allow_writes,
    }
    _append(store, ctx, event_id, SENT, sent_payload)

    history = _messages(store, ws_id, thread_id)
    if not any(m.get("in_reply_to") == sent_id for m in history):
        upto = next(i for i, m in enumerate(history) if m["message_id"] == sent_id) + 1
        answer = reply(provider, config, ctx, history[:upto], allow_writes=allow_writes)
        reply_event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chat.replied:{event_id}"))
        _append(
            store,
            ctx,
            reply_event_id,
            REPLIED,
            {
                "thread_id": thread_id,
                "message_id": _message_id(reply_event_id),
                "in_reply_to": sent_id,
                "text": answer.text,
                "tool_calls": answer.tool_calls,
                "llm": answer.llm.to_dict(),
            },
        )
    messages = _messages(store, ws_id, thread_id)
    sent = next(m for m in messages if m["message_id"] == sent_id)
    answered = next(m for m in messages if m.get("in_reply_to") == sent_id)
    return SendResult(sent=sent, reply=answered)
