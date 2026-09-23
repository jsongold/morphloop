"""The v0.1 chain end to end, on fakes only (ADR-0012 gate, AC-A3, AC-B2, AC-F2).

pack loaded -> session -> attempt with a lab -> content opened and highlighted ->
tutor question -> submit -> checks, evaluator, evidence, learner-model update ->
timeline and resume state.
"""

from __future__ import annotations

from loop_harness import ACTIVITY_ID, LEARNER_ID, SKILL_ID, build_loop

HIGHLIGHT_PAYLOAD = {
    "highlight_id": "hl_first",
    "content_id": "concept.resolver",
    "content_version": "1",
    "selected_text": "stub resolver",
    "start_offset": 4,
    "end_offset": 17,
    "semantic_anchor": "sections[0]",
    "context_before": "The ",
    "context_after": " forwards queries.",
}


def test_full_v01_chain_records_every_step() -> None:
    fixture = build_loop()
    loop = fixture.loop

    session = loop.start_session(learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1")
    session_id = session.session.session_id

    activities = loop.session_activities(session_id)
    assert [view.activity_definition_id for view in activities] == [ACTIVITY_ID]
    assert set(activities[0].to_dict()) == {
        "activity_definition_id",
        "activity_definition_hash",
        "title",
        "activity_type",
        "skill_ids",
        "lab_backed",
        "instructions",
    }

    attempt = loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    assert attempt.status == "active"
    assert attempt.lab is not None and attempt.lab.status == "ready"
    assert fixture.labs.labs, "the lab runtime was asked to start a lab"

    loop.append_client_event(
        session_id=session_id,
        event_type="content.opened",
        event_version=1,
        payload={
            "content_id": "concept.resolver",
            "content_version": "1",
            "pane": "side",
            "source_highlight_id": None,
        },
        attempt_id=attempt.attempt_id,
        idempotency_key="web:3",
        occurred_at=fixture.clock(),
    )
    loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=HIGHLIGHT_PAYLOAD,
        attempt_id=attempt.attempt_id,
        idempotency_key="web:4",
        occurred_at=fixture.clock(),
    )
    loop.append_client_event(
        session_id=session_id,
        event_type="visualization.step_selected",
        event_version=1,
        payload={"visualization_id": "resolution-flow", "content_version": "1", "step_id": "ask"},
        attempt_id=attempt.attempt_id,
        idempotency_key="web:5",
        occurred_at=fixture.clock(),
    )

    exchange = loop.send_chat_message(
        session_id=session_id,
        text="Why does the name not resolve?",
        references=[{"type": "highlight", "id": "hl_first"}],
        attempt_id=attempt.attempt_id,
        idempotency_key="web:6",
    )
    assert exchange.reply.payload["mode"] == "hint"
    assert exchange.reply.causation_id == exchange.request.event_id

    submitted = loop.submit_attempt(attempt_id=attempt.attempt_id, idempotency_key="web:7")
    assert submitted.status == "evaluating"

    completed = loop.evaluate_attempt(attempt.attempt_id)
    assert completed.status == "completed"
    assert completed.result is not None
    result = completed.result
    assert result.outcome == "passed"
    assert len(result.evidence) == 1
    assert len(result.skill_updates) == 1
    evidence_event = result.evidence[0]
    update_payload = result.skill_updates[0].payload
    assert update_payload["evidence_ids"] == [evidence_event.payload["evidence_id"]]
    assert result.skill_updates[0].causation_id == evidence_event.event_id
    assert evidence_event.causation_id == result.evaluation.event_id

    checks = result.evaluation.payload["checks"]
    assert isinstance(checks, list) and checks and checks[0]["passed"] is True

    skills = loop.learner_skills(LEARNER_ID)
    assert [skill.skill_id for skill in skills] == [SKILL_ID]
    assert skills[0].update_count == 1

    page = loop.session_timeline(session_id)
    positions = [event.position for event in page.events]
    assert positions == sorted(positions)
    types = [event.event_type for event in page.events]
    assert types[:3] == ["session.started", "activity.started", "lab.started"]
    assert types[-1] == "activity.completed"
    assert page.has_more is False

    resumed = loop.session_state(session_id)
    assert resumed.active_attempt is None
    assert resumed.last_completed_attempt is not None
    assert resumed.last_completed_attempt.attempt_id == attempt.attempt_id
    assert resumed.ui_state.open_content is not None
    assert resumed.ui_state.open_content["content_id"] == "concept.resolver"
    assert [step["step_id"] for step in resumed.ui_state.visualization_steps] == ["ask"]
    assert resumed.last_position == page.events[-1].position

    highlights = loop.session_highlights(session_id)
    assert [event.payload["highlight_id"] for event in highlights] == ["hl_first"]
    chat = loop.session_chat(session_id)
    assert [event.event_type for event in chat] == [
        "assistant.message_requested",
        "assistant.message_generated",
    ]


def test_session_started_carries_session_constant_provenance() -> None:
    fixture = build_loop()
    session = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    events = fixture.store.read_session(session.session.session_id)
    provenance = events[0].payload["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["harness_version"] == "0.1.0-test"
    assert provenance["pack"]["pack_content_hash"] == fixture.pack.content_hash
    assert provenance["registry_implementations"]["learner_model"] == "llm-learner-model@0.1.0"
    assert provenance["domain_adapters"] == {"fake": "0.1.0"}


def test_lab_reset_replaces_the_instance() -> None:
    fixture = build_loop()
    loop = fixture.loop
    session = loop.start_session(learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1")
    attempt = loop.start_attempt(
        session_id=session.session.session_id,
        activity_definition_id=ACTIVITY_ID,
        idempotency_key="web:2",
    )
    assert attempt.lab is not None
    first = attempt.lab.lab_instance_id

    replacement = loop.reset_lab(lab_instance_id=first, idempotency_key="web:3")
    assert replacement.lab_instance_id != first
    assert loop.lab_state(first).replaced_by_lab_instance_id == replacement.lab_instance_id
    assert loop.attempt_state(attempt.attempt_id).lab is not None
    assert loop.attempt_state(attempt.attempt_id).lab.lab_instance_id == replacement.lab_instance_id

    started = [
        event
        for event in fixture.store.read_session(session.session.session_id)
        if event.event_type == "lab.started"
    ]
    assert [event.payload["trigger"] for event in started] == ["initial", "reset"]
    assert started[1].payload["replaces_lab_instance_id"] == first
    assert started[1].payload["provenance"]["fixture_id"] == "fake.lab"
