"""The append path and the projections: idempotency, rebuild, redaction, AC-E4.

AC-F5 (idempotent resend), AC-F6 (rebuild equals the running state), the NUL
rejection of the redaction step and "an LLM output that fails validation changes
no state".
"""

from __future__ import annotations

from typing import Any

import pytest
from loop_harness import ACTIVITY_ID, LEARNER_ID, LoopFixture, RoleLLM, build_loop

from harness.core.loop import LOOP_PROJECTIONS, EventRejectedError, LLMFailedError
from harness.core.ports import EventStore, JsonObject

HIGHLIGHT = {
    "highlight_id": "hl_one",
    "content_id": "concept.resolver",
    "content_version": "1",
    "selected_text": "stub resolver",
    "start_offset": 0,
    "end_offset": 13,
    "semantic_anchor": None,
    "context_before": "",
    "context_after": "",
}


def projections(store: EventStore) -> dict[str, dict[str, JsonObject]]:
    with store.transaction() as tx:
        return {name: dict(tx.list_projection(name)) for name in LOOP_PROJECTIONS}


def start_attempt(fixture: LoopFixture) -> tuple[str, str]:
    session = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    session_id = session.session.session_id
    attempt = fixture.loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    return session_id, attempt.attempt_id


def run_to_completion(fixture: LoopFixture) -> tuple[str, str]:
    session_id, attempt_id = start_attempt(fixture)
    fixture.loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=HIGHLIGHT,
        attempt_id=attempt_id,
        idempotency_key="web:3",
        occurred_at=fixture.clock(),
    )
    fixture.loop.send_chat_message(
        session_id=session_id,
        text="Where should I look first?",
        references=[{"type": "highlight", "id": "hl_one"}],
        attempt_id=attempt_id,
        idempotency_key="web:4",
    )
    fixture.loop.submit_attempt(attempt_id=attempt_id, idempotency_key="web:5")
    fixture.loop.evaluate_attempt(attempt_id)
    return session_id, attempt_id


def test_resending_the_same_idempotency_key_appends_no_second_event() -> None:
    fixture = build_loop()
    session_id, attempt_id = start_attempt(fixture)
    before = len(fixture.store.read_session(session_id))

    first = fixture.loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=HIGHLIGHT,
        attempt_id=attempt_id,
        idempotency_key="web:same",
        occurred_at=fixture.clock(),
    )
    again = fixture.loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=HIGHLIGHT,
        attempt_id=attempt_id,
        idempotency_key="web:same",
        occurred_at=fixture.clock(),
    )
    assert again.event_id == first.event_id
    assert again.position == first.position
    assert len(fixture.store.read_session(session_id)) == before + 1


def test_resending_a_session_start_returns_the_same_session() -> None:
    fixture = build_loop()
    first = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    again = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    assert again.session.session_id == first.session.session_id
    assert len(fixture.store.read_all()) == 1


def test_rebuilt_projections_equal_the_running_state() -> None:
    fixture = build_loop()
    session_id, attempt_id = run_to_completion(fixture)
    live = projections(fixture.store)

    replayed = fixture.loop.rebuild()
    assert replayed == len(fixture.store.read_all())
    assert projections(fixture.store) == live

    # The learner model is never called again during a rebuild (ADR-0013).
    roles = [request.role for request in fixture.llm.requests]
    assert roles.count("learner_model") == 1
    assert fixture.loop.session_state(session_id).last_completed_attempt is not None
    assert fixture.loop.attempt_state(attempt_id).status == "completed"


def test_rebuild_from_an_empty_projection_store_restores_highlights_and_chat() -> None:
    fixture = build_loop()
    session_id, _ = run_to_completion(fixture)
    highlights = [event.event_id for event in fixture.loop.session_highlights(session_id)]
    chat = [event.event_id for event in fixture.loop.session_chat(session_id)]

    with fixture.store.transaction() as tx:
        for name in LOOP_PROJECTIONS:
            tx.clear_projection(name)
    fixture.loop.rebuild()

    assert [e.event_id for e in fixture.loop.session_highlights(session_id)] == highlights
    assert [e.event_id for e in fixture.loop.session_chat(session_id)] == chat


def test_a_nul_in_a_payload_string_is_refused_and_nothing_is_appended() -> None:
    fixture = build_loop()
    session_id, attempt_id = start_attempt(fixture)
    before = len(fixture.store.read_session(session_id))
    payload: dict[str, Any] = {**HIGHLIGHT, "selected_text": "stub\x00resolver"}

    with pytest.raises(EventRejectedError):
        fixture.loop.append_client_event(
            session_id=session_id,
            event_type="content.highlighted",
            event_version=1,
            payload=payload,
            attempt_id=attempt_id,
            idempotency_key="web:nul",
            occurred_at=fixture.clock(),
        )
    assert len(fixture.store.read_session(session_id)) == before
    assert fixture.loop.session_highlights(session_id) == []


def test_a_payload_that_fails_its_contract_schema_is_refused() -> None:
    fixture = build_loop()
    session_id, attempt_id = start_attempt(fixture)
    broken = {key: value for key, value in HIGHLIGHT.items() if key != "end_offset"}
    with pytest.raises(Exception, match="end_offset"):
        fixture.loop.append_client_event(
            session_id=session_id,
            event_type="content.highlighted",
            event_version=1,
            payload=broken,
            attempt_id=attempt_id,
            idempotency_key="web:broken",
            occurred_at=fixture.clock(),
        )


def test_an_invalid_learner_model_output_changes_no_state() -> None:
    llm = RoleLLM(
        learner_model={
            "next": {"mastery_probability": 0.5, "uncertainty": 0.4},
            "evidence_ids": [],  # the schema requires at least one
            "rationale": "no evidence",
            "predicted_success_probability": 0.5,
        }
    )
    fixture = build_loop(llm=llm)
    session_id, attempt_id = start_attempt(fixture)
    fixture.loop.submit_attempt(attempt_id=attempt_id, idempotency_key="web:5")
    before = len(fixture.store.read_session(session_id))

    with pytest.raises(LLMFailedError):
        fixture.loop.evaluate_attempt(attempt_id)

    # Only the failure itself is recorded; no evaluation chain, no learner state.
    events = fixture.store.read_session(session_id)
    assert [event.event_type for event in events[before:]] == ["evaluation.failed"]
    assert fixture.loop.learner_skills(LEARNER_ID) == []
    state = fixture.loop.attempt_state(attempt_id)
    assert state.status == "active"
    assert state.last_submission_error is not None
    assert state.last_submission_error["code"] == "llm-failed"
    assert state.result is None
    live = projections(fixture.store)
    fixture.loop.rebuild()
    assert projections(fixture.store) == live


def test_an_invalid_evaluator_output_changes_no_state() -> None:
    llm = RoleLLM(evaluator={"success": True, "rationale": "", "evidence": []})
    fixture = build_loop(llm=llm)
    session_id, attempt_id = start_attempt(fixture)
    fixture.loop.submit_attempt(attempt_id=attempt_id, idempotency_key="web:5")
    before = len(fixture.store.read_session(session_id))

    with pytest.raises(LLMFailedError):
        fixture.loop.evaluate_attempt(attempt_id)
    events = fixture.store.read_session(session_id)
    assert [event.event_type for event in events[before:]] == ["evaluation.failed"]
    assert [request.role for request in llm.requests] == ["evaluator"]
