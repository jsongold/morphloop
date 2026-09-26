"""Chat: send a message, get the Assistant's reply, list a thread (#63).

Threads belong to the ws resource; chat resolves one through its own
``chat.thread`` view (:mod:`harness.core.chat.view`), built from the ws's
``thread.created`` events (opaque ids; no import of that resource). The
lookup always runs inside a transaction alongside the work it gates -- never
a bare ``EventStoreV2.read()`` -- so it never needs a second pooled
connection while another request holds one on the same store (#104).

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
from harness.core.chat.view import REPLIED, SENT, ChatMessagesView, ChatThreadView
from harness.core.ports.events_v2 import EventStoreV2, EventTransactionV2, EventV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMToolProvider
from harness.core.view import dispatch


class ThreadNotFoundError(LookupError):
    """No ``thread.created`` for this thread id in this ws."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SendResult:
    sent: JsonObject
    reply: JsonObject


def _find_thread(
    tx: EventTransactionV2, store: EventStoreV2, user_id: str, ws_id: str, thread_id: str
) -> ToolContext:
    """``find_thread``, given an already-open transaction to read the view on."""
    doc = ChatThreadView.get(tx, thread_id)
    if doc is None or doc["ws_id"] != ws_id or doc["user_id"] != user_id:
        raise ThreadNotFoundError(f"no thread {thread_id!r} in ws {ws_id!r}")
    target = doc.get("target")
    labels = doc.get("labels")
    session_id = doc.get("session_id")
    return ToolContext(
        store=store,
        user_id=user_id,
        session_id=str(session_id) if session_id is not None else None,
        ws_id=ws_id,
        thread_id=thread_id,
        target=target if isinstance(target, dict) else None,
        labels=tuple(str(x) for x in labels) if isinstance(labels, list) else (),
    )


def find_thread(store: EventStoreV2, user_id: str, ws_id: str, thread_id: str) -> ToolContext:
    """The thread as a tool context (its user, session, target and labels)."""
    with store.transaction() as tx:
        return _find_thread(tx, store, user_id, ws_id, thread_id)


def list_messages(
    store: EventStoreV2, user_id: str, ws_id: str, thread_id: str
) -> Sequence[JsonObject]:
    """The thread's messages; raises :class:`ThreadNotFoundError`."""
    with store.transaction() as tx:
        _find_thread(tx, store, user_id, ws_id, thread_id)
        return list(ChatMessagesView.messages(tx, ws_id, thread_id))


def list_messages_page(
    store: EventStoreV2,
    user_id: str,
    ws_id: str,
    thread_id: str,
    *,
    after: str | None,
    limit: int,
) -> tuple[Sequence[JsonObject], str | None]:
    """One page of the thread's messages plus the next cursor (a view key);
    raises :class:`ThreadNotFoundError`."""
    with store.transaction() as tx:
        _find_thread(tx, store, user_id, ws_id, thread_id)
        return ChatMessagesView.page(tx, ws_id, thread_id, after=after, limit=limit)


def _append(
    tx: EventTransactionV2, ctx: ToolContext, event_id: str, type_: str, payload: JsonObject
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

    The thread lookup, the ``chat.sent`` append and the pre-reply history read
    share one transaction. ``chat.replied`` commits in a second transaction
    after the LLM call, so a slow or failing call never holds one (#63).

    Raises :class:`ThreadNotFoundError`, ``EventIdConflictError`` (same id,
    other content), ``LLMError`` / ``AssistantError`` (no reply recorded).
    """
    sent_id = _message_id(event_id)
    sent_payload: JsonObject = {
        "thread_id": thread_id,
        "message_id": sent_id,
        "text": text,
        "allow_writes": allow_writes,
    }
    with store.transaction() as tx:
        ctx = _find_thread(tx, store, user_id, ws_id, thread_id)
        _append(tx, ctx, event_id, SENT, sent_payload)
        history = list(ChatMessagesView.messages(tx, ws_id, thread_id))

    if not any(m.get("in_reply_to") == sent_id for m in history):
        upto = next(i for i, m in enumerate(history) if m["message_id"] == sent_id) + 1
        answer = reply(provider, config, ctx, history[:upto], allow_writes=allow_writes)
        reply_event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chat.replied:{event_id}"))
        with store.transaction() as tx:
            _append(
                tx,
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
            messages = list(ChatMessagesView.messages(tx, ws_id, thread_id))
    else:
        with store.transaction() as tx:
            messages = list(ChatMessagesView.messages(tx, ws_id, thread_id))

    sent = next(m for m in messages if m["message_id"] == sent_id)
    answered = next(m for m in messages if m.get("in_reply_to") == sent_id)
    return SendResult(sent=sent, reply=answered)
