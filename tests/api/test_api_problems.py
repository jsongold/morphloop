"""Errors and idempotent replays (AC-F5, AC-E4, RFC 9457).

Every failure is `application/problem+json` with the closed `code` vocabulary of
``contracts/openapi/v0.1.yaml``, and every resend of an `idempotency_key`
returns the original body with `Idempotent-Replayed: true` without appending a
second event.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from api_harness import assert_component, build_app
from fastapi.testclient import TestClient
from loop_harness import ACTIVITY_ID, LEARNER_ID, PACK_ID, LoopFixture, build_loop

from harness.core.ports import LLMError

NOW = "2026-09-23T09:30:00Z"
HEADER = "Idempotent-Replayed"


def _session_body(fixture: LoopFixture, key: str = "web:1") -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "learner_id": LEARNER_ID,
        "pack_id": PACK_ID,
        "pack_content_hash": fixture.pack.content_hash,
    }


@pytest.fixture
def wired() -> Iterator[tuple[TestClient, LoopFixture]]:
    app, fixture = build_app()
    with TestClient(app) as client:
        yield client, fixture


def _assert_problem(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert_component(body, "Problem")
    assert body["code"] == code
    return body


# --- problem responses ------------------------------------------------------


def test_unknown_session_is_not_found(wired: tuple[TestClient, LoopFixture]) -> None:
    client, _ = wired

    _assert_problem(client.get("/sessions/ses_nope"), 404, "not-found")


def test_unknown_content_kind_is_not_found(wired: tuple[TestClient, LoopFixture]) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]

    response = client.get(f"/sessions/{session_id}/content/reference_solution/fix-resolver")

    _assert_problem(response, 404, "not-found")


def test_second_open_attempt_is_a_state_conflict(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]
    body = {"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID}
    assert client.post(f"/sessions/{session_id}/attempts", json=body).status_code == 201

    second = client.post(
        f"/sessions/{session_id}/attempts",
        json={"idempotency_key": "web:3", "activity_definition_id": ACTIVITY_ID},
    )

    _assert_problem(second, 409, "state-conflict")


def test_unknown_body_field_fails_validation(wired: tuple[TestClient, LoopFixture]) -> None:
    client, fixture = wired

    response = client.post("/sessions", json=_session_body(fixture) | {"extra": 1})

    body = _assert_problem(response, 422, "validation-failed")
    assert [error["path"] for error in body["errors"]] == ["$.extra"]


def test_event_payload_failing_its_contract_schema_fails_validation(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]

    response = client.post(
        f"/sessions/{session_id}/events",
        json={
            "event_type": "content.opened",
            "event_version": 1,
            "occurred_at": NOW,
            "idempotency_key": "web:2",
            "attempt_id": None,
            "payload": {"content_id": "concept.resolver"},
        },
    )

    body = _assert_problem(response, 422, "validation-failed")
    assert any(error["path"].startswith("$.payload") for error in body["errors"])


def test_malformed_json_is_an_invalid_request(wired: tuple[TestClient, LoopFixture]) -> None:
    client, _ = wired

    response = client.post(
        "/sessions", content=b"{not json", headers={"Content-Type": "application/json"}
    )

    _assert_problem(response, 400, "invalid-request")


def test_out_of_range_query_parameter_is_an_invalid_request(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]

    response = client.get(f"/sessions/{session_id}/timeline", params={"limit": 501})

    _assert_problem(response, 400, "invalid-request")


def test_a_failing_tutor_is_reported_as_llm_failed(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]
    fixture.llm.set("tutor", LLMError("the model is unreachable"))

    response = client.post(
        f"/sessions/{session_id}/chat/messages",
        json={
            "idempotency_key": "web:2",
            "occurred_at": NOW,
            "thread_id": None,
            "attempt_id": None,
            "text": "What is a resolver?",
            "references": [],
        },
    )

    _assert_problem(response, 502, "llm-failed")
    # The request event stays recorded; only the reply is missing (OpenAPI).
    chat = client.get(f"/sessions/{session_id}/chat").json()
    assert [event["event_type"] for event in chat["events"]] == ["assistant.message_requested"]


def test_a_failed_evaluation_leaves_the_attempt_active_with_a_problem(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    """AC-E4: the failure is recorded as ``evaluation.failed``, so
    ``last_submission_error`` survives a restart and shows on the timeline."""
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]
    attempt_id = client.post(
        f"/sessions/{session_id}/attempts",
        json={"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID},
    ).json()["attempt_id"]
    fixture.llm.set("evaluator", LLMError("the model is unreachable"))

    assert (
        client.post(f"/attempts/{attempt_id}/submit", json={"idempotency_key": "web:3"})
    ).status_code == 202

    state = client.get(f"/attempts/{attempt_id}").json()
    assert_component(state, "AttemptState")
    assert state["status"] == "active"
    assert state["last_submission_error"]["code"] == "llm-failed"
    timeline = client.get(f"/sessions/{session_id}/timeline", params={"limit": 500}).json()
    types = [event["event_type"] for event in timeline["events"]]
    assert "evaluation.completed" not in types
    assert "learner_skill.updated" not in types
    assert types[-1] == "evaluation.failed"


# --- idempotent replays -----------------------------------------------------


def test_repeating_a_session_start_replays_the_original_response(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired

    first = client.post("/sessions", json=_session_body(fixture))
    second = client.post("/sessions", json=_session_body(fixture))

    assert HEADER not in first.headers
    assert first.status_code == second.status_code == 201
    assert second.headers[HEADER] == "true"
    assert second.json()["session"]["session_id"] == first.json()["session"]["session_id"]


def test_repeating_a_command_replays_without_appending(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]
    body = {"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID}
    first = client.post(f"/sessions/{session_id}/attempts", json=body)
    second = client.post(f"/sessions/{session_id}/attempts", json=body)

    assert second.status_code == 201
    assert second.headers[HEADER] == "true"
    assert second.json()["attempt_id"] == first.json()["attempt_id"]
    timeline = client.get(f"/sessions/{session_id}/timeline", params={"limit": 500}).json()
    started = [e for e in timeline["events"] if e["event_type"] == "activity.started"]
    assert len(started) == 1


def test_repeating_a_client_event_returns_the_stored_event(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = client.post("/sessions", json=_session_body(fixture)).json()["session"][
        "session_id"
    ]
    body = {
        "event_type": "visualization.step_selected",
        "event_version": 1,
        "occurred_at": NOW,
        "idempotency_key": "web:2",
        "attempt_id": None,
        "payload": {
            "visualization_id": "resolution-flow",
            "content_version": "1",
            "step_id": "ask",
        },
    }
    first = client.post(f"/sessions/{session_id}/events", json=body)
    second = client.post(f"/sessions/{session_id}/events", json=body)

    assert first.status_code == 201, first.text
    assert second.headers[HEADER] == "true"
    assert second.json() == first.json()


def test_the_loop_fixture_is_isolated_per_test() -> None:
    """Two apps never share a store, so a replay in one is not a replay in the other."""
    app_one, fixture_one = build_app()
    app_two, fixture_two = build_app(build_loop())

    with TestClient(app_one) as one, TestClient(app_two) as two:
        assert one.post("/sessions", json=_session_body(fixture_one)).status_code == 201
        response = two.post("/sessions", json=_session_body(fixture_two))

    assert HEADER not in response.headers
