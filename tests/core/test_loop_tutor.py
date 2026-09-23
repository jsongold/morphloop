"""Tutor context and mode, highlights and chat replay (AC-D2, AC-D3, AC-D5, AC-E1..E3, AC-J6)."""

from __future__ import annotations

import pytest
from loop_harness import (
    ACTIVITY_ID,
    LEARNER_ID,
    SOLUTION_SECRET,
    LoopFixture,
    RoleLLM,
    build_loop,
)

from harness.core.loop import InvalidRequestError, LLMFailedError

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


def started(fixture: LoopFixture) -> tuple[str, str]:
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


def test_the_reference_solution_never_reaches_the_tutor_context() -> None:
    llm = RoleLLM()
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)

    fixture.loop.send_chat_message(
        session_id=session_id,
        text="How do I fix this?",
        references=[{"type": "highlight", "id": "hl_quote"}],
        attempt_id=attempt_id,
        requested_mode="explain",
        idempotency_key="web:4",
    )
    request = [r for r in llm.requests if r.role == "tutor"][-1]
    rendered = "\n".join(message.content for message in request.messages)
    assert SOLUTION_SECRET not in rendered
    assert "reference_solution" not in rendered
    assert "127.0.0.53" not in rendered

    context = llm.context_of("tutor")
    assert context["mission"]["activity_definition_id"] == ACTIVITY_ID
    assert set(context["mission"]["activity"]) <= {
        "title",
        "activity_type",
        "skills",
        "difficulty",
        "instructions",
        "hints",
        "remediation",
    }
    assert context["selected_content"]["highlights"][0]["highlight_id"] == "hl_quote"
    assert [event["event_type"] for event in context["recent_events"]][0] == "session.started"


def test_the_evaluator_may_receive_the_reference_solution_when_the_pack_says_so() -> None:
    llm = RoleLLM()
    fixture = build_loop(llm=llm)
    _, attempt_id = started(fixture)
    fixture.loop.submit_attempt(attempt_id=attempt_id, idempotency_key="web:5")
    fixture.loop.evaluate_attempt(attempt_id)

    context = llm.context_of("evaluator")
    assert context["reference_solution"]["explanation"] == SOLUTION_SECRET


def test_hint_mode_is_forced_while_the_attempt_is_unfinished() -> None:
    llm = RoleLLM()
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)

    exchange = fixture.loop.send_chat_message(
        session_id=session_id,
        text="Just tell me the answer.",
        references=[],
        attempt_id=attempt_id,
        requested_mode="review",  # declared by the pack, but not allowed unfinished
        idempotency_key="web:4",
    )
    assert exchange.reply.payload["mode"] == "hint"
    assert llm.context_of("tutor")["mode"] == "hint"

    allowed = fixture.loop.send_chat_message(
        session_id=session_id,
        text="Explain how resolution works.",
        references=[],
        attempt_id=attempt_id,
        requested_mode="explain",
        idempotency_key="web:5",
    )
    assert allowed.reply.payload["mode"] == "explain"


def test_a_mode_the_pack_does_not_declare_is_rejected() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    with pytest.raises(InvalidRequestError):
        fixture.loop.send_chat_message(
            session_id=session_id,
            text="?",
            references=[],
            attempt_id=attempt_id,
            requested_mode="solution",
            idempotency_key="web:4",
        )


def test_after_completion_the_default_mode_is_the_finished_one() -> None:
    llm = RoleLLM()
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)
    fixture.loop.submit_attempt(attempt_id=attempt_id, idempotency_key="web:5")
    fixture.loop.evaluate_attempt(attempt_id)

    exchange = fixture.loop.send_chat_message(
        session_id=session_id,
        text="What did I miss?",
        references=[],
        attempt_id=attempt_id,
        idempotency_key="web:6",
    )
    assert exchange.reply.payload["mode"] == "review"


def test_highlights_are_persisted_and_recovered_after_a_rebuild() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)

    stored = fixture.loop.session_highlights(session_id)
    assert len(stored) == 1
    assert stored[0].payload["selected_text"] == "stub resolver"
    assert stored[0].attempt_id == attempt_id

    fixture.loop.rebuild()
    recovered = fixture.loop.session_highlights(session_id)
    assert [event.to_dict() for event in recovered] == [event.to_dict() for event in stored]


def test_a_reply_may_only_cite_what_it_was_given() -> None:
    llm = RoleLLM(
        tutor={
            "text": "See your earlier note.",
            "references": [{"type": "highlight", "id": "hl_other"}],
        }
    )
    fixture = build_loop(llm=llm)
    session_id, attempt_id = started(fixture)
    highlight(fixture, session_id, attempt_id)
    with pytest.raises(LLMFailedError):
        fixture.loop.send_chat_message(
            session_id=session_id,
            text="?",
            references=[{"type": "highlight", "id": "hl_quote"}],
            attempt_id=attempt_id,
            idempotency_key="web:4",
        )


def test_resending_a_chat_message_appends_no_second_request_event() -> None:
    fixture = build_loop()
    session_id, attempt_id = started(fixture)
    first = fixture.loop.send_chat_message(
        session_id=session_id,
        text="Where do I start?",
        references=[],
        attempt_id=attempt_id,
        idempotency_key="web:4",
    )
    again = fixture.loop.send_chat_message(
        session_id=session_id,
        text="Where do I start?",
        references=[],
        attempt_id=attempt_id,
        idempotency_key="web:4",
    )
    assert again.request.event_id == first.request.event_id
    assert again.reply.event_id == first.reply.event_id
    assert len(fixture.loop.session_chat(session_id)) == 2
