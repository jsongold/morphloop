"""The read API the HTTP layer serves, and the state conflicts it must report.

Content views (AC-C1, AC-D1, AC-J6), timeline paging (AC-F2, AC-F7), resume
lists (AC-F4) and the ``409 state-conflict`` cases of the OpenAPI contract.
"""

from __future__ import annotations

import pytest
from loop_harness import ACTIVITY_ID, LEARNER_ID, PACK_ID, SKILL_ID, LoopFixture, build_loop

from harness.core.loop import NotFoundError, StateConflictError


def session_of(fixture: LoopFixture, key: str = "web:1") -> str:
    state = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key=key
    )
    return state.session.session_id


def test_only_learner_facing_content_has_a_route() -> None:
    fixture = build_loop()
    session_id = session_of(fixture)

    summaries = fixture.loop.session_content(session_id)
    assert {(item.kind, item.definition_id) for item in summaries} == {
        ("skill", SKILL_ID),
        ("visualization", "resolution-flow"),
        ("reference", "concept.resolver"),
    }
    assert [item.kind for item in fixture.loop.session_content(session_id, "reference")] == [
        "reference"
    ]

    document = fixture.loop.session_content_document(session_id, "visualization", "resolution-flow")
    assert document.summary.content_version == "1"
    assert document.document["diagram"]["steps"][0]["reality"]["observe"]

    for kind in ("activity", "environment", "evaluator", "reference_solution"):
        with pytest.raises(NotFoundError):
            fixture.loop.session_content(session_id, kind)
    with pytest.raises(NotFoundError):
        fixture.loop.session_content_document(session_id, "reference", "nope")


def test_layout_and_pack_list_are_served_from_the_pack_projection() -> None:
    fixture = build_loop()
    session_id = session_of(fixture)
    layout = fixture.loop.session_layout(session_id)
    assert layout is not None and layout["panes"][0]["id"] == "terminal"

    packs = fixture.loop.list_packs()
    assert [view.pack.pack_id for view in packs] == [PACK_ID]
    assert packs[0].to_dict()["pack"]["pack_content_hash"] == fixture.pack.content_hash


def test_learner_sessions_are_listed_most_recent_first() -> None:
    fixture = build_loop()
    first = session_of(fixture, "web:1")
    second = session_of(fixture, "web:2")
    assert [view.session_id for view in fixture.loop.learner_sessions(LEARNER_ID)] == [
        second,
        first,
    ]
    assert fixture.loop.learner_sessions("usr_nobody") == []


def test_the_timeline_pages_forward_without_gaps() -> None:
    fixture = build_loop()
    session_id = session_of(fixture)
    fixture.loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    everything = fixture.loop.session_timeline(session_id, limit=500)

    seen = []
    cursor, has_more = 0, True
    while has_more:
        page = fixture.loop.session_timeline(session_id, after_position=cursor, limit=1)
        seen.extend(event.event_id for event in page.events)
        cursor, has_more = page.last_position, page.has_more
    assert seen == [event.event_id for event in everything.events]

    with pytest.raises(Exception, match="limit"):
        fixture.loop.session_timeline(session_id, limit=501)
    with pytest.raises(NotFoundError):
        fixture.loop.session_timeline("ses_missing")


def test_state_conflicts_are_reported_not_silently_allowed() -> None:
    fixture = build_loop()
    session_id = session_of(fixture)
    attempt = fixture.loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    assert attempt.lab is not None

    with pytest.raises(StateConflictError):
        fixture.loop.start_attempt(
            session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:3"
        )

    replacement = fixture.loop.reset_lab(
        lab_instance_id=attempt.lab.lab_instance_id, idempotency_key="web:4"
    )
    with pytest.raises(StateConflictError):
        fixture.loop.reset_lab(lab_instance_id=attempt.lab.lab_instance_id, idempotency_key="web:5")

    fixture.loop.submit_attempt(attempt_id=attempt.attempt_id, idempotency_key="web:6")
    with pytest.raises(StateConflictError):
        fixture.loop.submit_attempt(attempt_id=attempt.attempt_id, idempotency_key="web:7")
    with pytest.raises(StateConflictError):
        fixture.loop.reset_lab(lab_instance_id=replacement.lab_instance_id, idempotency_key="web:8")


def test_a_reused_highlight_id_is_a_conflict() -> None:
    fixture = build_loop()
    session_id = session_of(fixture)
    payload = {
        "highlight_id": "hl_dup",
        "content_id": "concept.resolver",
        "content_version": "1",
        "selected_text": "resolver",
        "start_offset": 0,
        "end_offset": 8,
        "semantic_anchor": None,
        "context_before": "",
        "context_after": "",
    }
    fixture.loop.append_client_event(
        session_id=session_id,
        event_type="content.highlighted",
        event_version=1,
        payload=payload,
        attempt_id=None,
        idempotency_key="web:2",
        occurred_at=fixture.clock(),
    )
    with pytest.raises(StateConflictError):
        fixture.loop.append_client_event(
            session_id=session_id,
            event_type="content.highlighted",
            event_version=1,
            payload={**payload, "selected_text": "other"},
            attempt_id=None,
            idempotency_key="web:3",
            occurred_at=fixture.clock(),
        )


def test_an_idempotency_key_cannot_be_reused_for_another_command() -> None:
    fixture = build_loop()
    session_id = session_of(fixture, "web:1")
    with pytest.raises(StateConflictError):
        fixture.loop.start_attempt(
            session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:1"
        )
