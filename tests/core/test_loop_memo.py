"""Highlight-thread memo summarization (issue #4, AC-D2, AC-J6).

chat in a highlight thread -> memo_summarizer -> memo.recorded -> learner edit
(memo.edited) stops rewrites -> GET-style session_memos and thread-filtered chat
-> rebuild consistency. Everything runs on fakes; the summarizer never sees the
reference solution during an unfinished attempt.
"""

from __future__ import annotations

import pytest
from loop_harness import (
    ACTIVITY_ID,
    LEARNER_ID,
    SOLUTION_SECRET,
    LoopFixture,
    RoleLLM,
    build_loop,
    memo_output,
)

from harness.core.loop import InvalidRequestError
from harness.core.ports import LLMError

HIGHLIGHT = {
    "highlight_id": "hl_quote",
    "content_id": "concept.resolver",
    "content_version": "1",
    "selected_text": "stub resolver",
    "start_offset": 0,
    "end_offset": 13,
    "semantic_anchor": None,
    "context_before": "",
    "context_after": "",
}

THREAD = "thr_quote"
MEMO = "memo_quote"


def started(fixture: LoopFixture, *, llm: RoleLLM | None = None) -> tuple[str, str]:
    session = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    session_id = session.session.session_id
    attempt = fixture.loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    return session_id, attempt.attempt_id


def highlight(fixture: LoopFixture, session_id: str, attempt_id: str, key: str = "web:3") -> None:
    fixture.loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=HIGHLIGHT,
        attempt_id=attempt_id,
        idempotency_key=key,
        occurred_at=fixture.clock(),
    )


def ask(
    fixture: LoopFixture,
    session_id: str,
    attempt_id: str,
    key: str,
    text: str = "What is this?",
):
    return fixture.loop.send_chat_message(
        session_id=session_id,
        text=text,
        references=[{"type": "highlight", "id": "hl_quote"}],
        attempt_id=attempt_id,
        thread_id=THREAD,
        idempotency_key=key,
    )


def memo_events(fixture: LoopFixture, session_id: str):
    return [
        event
        for event in fixture.store.read_session(session_id)
        if event.event_type == "memo.recorded"
    ]


def test_highlight_thread_conversation_records_a_memo() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)

    exchange = ask(fixture, session_id, attempt_id, key="web:4")
    assert exchange.memo is not None
    assert (exchange.memo.memo_id, exchange.memo.highlight_id, exchange.memo.thread_id) == (
        MEMO,
        "hl_quote",
        THREAD,
    )
    assert not exchange.memo.edited_by_learner
    assert exchange.memo.title and exchange.memo.body

    recorded = memo_events(fixture, session_id)
    assert len(recorded) == 1
    assert recorded[0].causation_id == exchange.reply.event_id
    assert recorded[0].actor == "system"
    payload = recorded[0].payload
    assert payload["memo_id"] == MEMO
    highlight_event = fixture.loop.session_highlights(session_id)[0]
    thread_events = fixture.loop.session_chat(session_id, thread_id=THREAD)
    assert payload["source_event_ids"] == (
        [highlight_event.event_id] + [event.event_id for event in thread_events]
    )
    assert len(payload["source_event_ids"]) >= 2

    memos = fixture.loop.session_memos(session_id)
    assert len(memos) == 1
    assert memos[0].to_dict() == exchange.memo.to_dict()


def test_the_next_reply_rewrites_the_memo() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)

    ask(fixture, session_id, attempt_id, key="web:4")
    ask(fixture, session_id, attempt_id, text="And how do I verify it?", key="web:5")

    recorded = memo_events(fixture, session_id)
    assert len(recorded) == 2
    assert len(fixture.loop.session_memos(session_id)) == 1
    assert [event.payload["memo_id"] for event in recorded] == [MEMO, MEMO]
    assert recorded[-1].payload["body"] == recorded[-1].payload["body"]


def test_a_learner_edit_stops_the_summarizer_from_rewriting() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)
    ask(fixture, session_id, attempt_id, key="web:4")

    fixture.loop.append_client_event(
        session_id=session_id,
        event_type="memo.edited",
        event_version=1,
        payload={"memo_id": MEMO, "title": "My note", "body": "I wrote this myself."},
        attempt_id=attempt_id,
        idempotency_key="web:5",
        occurred_at=fixture.clock(),
    )
    second = ask(fixture, session_id, attempt_id, text="Still here?", key="web:6")

    # The edit hands the memo to the learner: no further ChatExchange memo.
    assert second.memo is None
    assert len(memo_events(fixture, session_id)) == 1
    [memo] = fixture.loop.session_memos(session_id)
    assert memo.edited_by_learner is True
    assert (memo.title, memo.body) == ("My note", "I wrote this myself.")


def test_editing_an_unknown_memo_is_rejected() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    with pytest.raises(InvalidRequestError):
        fixture.loop.append_client_event(
            session_id=session_id,
            event_type="memo.edited",
            event_version=1,
            payload={"memo_id": MEMO, "title": "x", "body": "y"},
            attempt_id=attempt_id,
            idempotency_key="web:4",
            occurred_at=fixture.clock(),
        )


def test_a_summarizer_failure_returns_no_memo_and_the_next_reply_retries() -> None:
    llm = RoleLLM(memo_summarizer=LLMError("summarizer down"))
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)

    first = ask(fixture, session_id, attempt_id, key="web:4")
    assert first.memo is None
    assert first.reply is not None
    assert memo_events(fixture, session_id) == []

    llm.set("memo_summarizer", memo_output(title="Recovered", body="Back on track."))
    second = ask(fixture, session_id, attempt_id, text="Retry?", key="web:5")
    assert second.memo is not None
    assert second.memo.title == "Recovered"
    assert len(memo_events(fixture, session_id)) == 1


def test_a_non_highlight_thread_has_no_memo() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    exchange = fixture.loop.send_chat_message(
        session_id=session_id,
        text="General question.",
        references=[],
        attempt_id=attempt_id,
        thread_id=None,
        idempotency_key="web:3",
    )
    assert exchange.memo is None
    assert memo_events(fixture, session_id) == []
    assert fixture.loop.session_memos(session_id) == []


def test_the_reference_solution_never_reaches_the_summarizer_context() -> None:
    llm = RoleLLM()
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)
    ask(fixture, session_id, attempt_id, key="web:4")

    request = [r for r in llm.requests if r.role == "memo_summarizer"][-1]
    rendered = "\n".join(message.content for message in request.messages)
    assert SOLUTION_SECRET not in rendered
    assert "reference_solution" not in rendered
    assert "127.0.0.53" not in rendered

    context = llm.context_of("memo_summarizer")
    assert context["highlight"]["highlight_id"] == "hl_quote"
    assert [event["event_type"] for event in context["thread"]] == [
        "assistant.message_requested",
        "assistant.message_generated",
    ]


def test_memos_and_threads_survive_a_rebuild() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)
    ask(fixture, session_id, attempt_id, key="web:4")
    ask(fixture, session_id, attempt_id, text="More?", key="web:5")

    before = [memo.to_dict() for memo in fixture.loop.session_memos(session_id)]
    recorded_before = [event.to_dict() for event in memo_events(fixture, session_id)]

    fixture.loop.rebuild()
    assert [memo.to_dict() for memo in fixture.loop.session_memos(session_id)] == before
    assert [event.to_dict() for event in memo_events(fixture, session_id)] == recorded_before


def test_session_chat_filters_to_one_thread() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)
    ask(fixture, session_id, attempt_id, key="web:4")
    fixture.loop.send_chat_message(
        session_id=session_id,
        text="Elsewhere.",
        references=[],
        attempt_id=attempt_id,
        thread_id=None,
        idempotency_key="web:5",
    )

    assert len(fixture.loop.session_chat(session_id)) == 4
    quoted = fixture.loop.session_chat(session_id, thread_id=THREAD)
    assert len(quoted) == 2
    assert all(event.payload["thread_id"] == THREAD for event in quoted)
    assert fixture.loop.session_chat(session_id, thread_id="thr_nobody") == []
