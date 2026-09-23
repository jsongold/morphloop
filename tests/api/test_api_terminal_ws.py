"""The lab terminal WebSocket against ``contracts/schemas/ws/`` (AC-B1..B4, AC-C3).

Every frame the server sends is validated against ``ws/envelope/message.json``,
which dispatches the payload by ``type``, so an envelope or payload that drifts
from the contract fails here. No Docker and no PTY: the loop is wired to
``FakeTerminalBridge``, which echoes what is written to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from api_harness import assert_ws_message, build_app
from fastapi.testclient import TestClient
from loop_harness import ACTIVITY_ID, LEARNER_ID, PACK_ID, LoopFixture
from starlette.testclient import WebSocketDenialResponse

from harness.api.app import create_app
from harness.api.backend import Backend
from harness.core.loop import LearningLoop
from harness.core.registry.builtin import v01_algorithm_registry

PROTOCOL = 1
NOW = "2026-09-23T09:30:00Z"


def _client(message: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": message,
        "protocol_version": PROTOCOL,
        "correlation_id": None,
        "idempotency_key": None,
        "payload": payload,
    }


def _receive_until(ws: Any, message_type: str, limit: int = 20) -> dict[str, Any]:
    """The next message of ``message_type``, validating everything on the way."""
    seen: list[str] = []
    for _ in range(limit):
        message: dict[str, Any] = ws.receive_json()
        assert_ws_message(message)
        seen.append(message["type"])
        if message["type"] == message_type:
            return message
    raise AssertionError(f"no {message_type!r} within {limit} messages, saw {seen}")


def _ready(ws: Any) -> dict[str, Any]:
    """Drain until the ``lab.status`` that carries the terminal id (ws/README.md)."""
    while True:
        message = _receive_until(ws, "lab.status")
        if message["payload"]["terminal_id"] is not None:
            return message


@pytest.fixture
def wired() -> Iterator[tuple[TestClient, LoopFixture, str, str]]:
    """A client with a session and a started, lab-backed attempt."""
    app, fixture = build_app()
    with TestClient(app) as client:
        session = client.post(
            "/sessions",
            json={
                "idempotency_key": "web:1",
                "learner_id": LEARNER_ID,
                "pack_id": PACK_ID,
                "pack_content_hash": fixture.pack.content_hash,
            },
        ).json()
        session_id = session["session"]["session_id"]
        attempt = client.post(
            f"/sessions/{session_id}/attempts",
            json={"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID},
        ).json()
        yield client, fixture, session_id, attempt["lab"]["lab_instance_id"]


def test_unknown_lab_is_refused_at_the_handshake_with_404(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    client, _, _, _ = wired

    with pytest.raises(WebSocketDenialResponse) as denial:
        with client.websocket_connect("/labs/lab_missing/terminal"):
            pass  # pragma: no cover - the handshake never completes

    assert denial.value.status_code == 404
    assert denial.value.json()["code"] == "not-found"


def test_terminal_streams_output_and_answers_ping(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    client, fixture, _, lab_id = wired

    with client.websocket_connect(f"/labs/{lab_id}/terminal") as ws:
        ready = _ready(ws)
        assert ready["payload"]["status"] == "ready"
        assert ready["payload"]["lab_instance_id"] == lab_id
        assert ready["payload"]["terminal_id"].startswith("term_")

        ws.send_json(_client("terminal.input", {"data": "cat /etc/resolv.conf\n"}))
        output = _receive_until(ws, "terminal.output")
        assert output["payload"]["data"] == "cat /etc/resolv.conf\n"
        assert output["payload"]["encoding"] == "utf-8"

        ws.send_json(_client("terminal.resize", {"cols": 120, "rows": 40}))
        ws.send_json(_client("ping", {}))
        _receive_until(ws, "pong")

    assert fixture.terminals.sessions[0].size.cols == 120


def test_terminal_input_becomes_a_recorded_command(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    client, _, session_id, lab_id = wired

    with client.websocket_connect(f"/labs/{lab_id}/terminal") as ws:
        _ready(ws)
        ws.send_json(_client("terminal.input", {"data": "dig corp.internal\n"}))
        _receive_until(ws, "terminal.output")

    timeline = client.get(f"/sessions/{session_id}/timeline", params={"limit": 500}).json()
    commands = [e for e in timeline["events"] if e["event_type"] == "terminal.command"]
    assert [e["payload"]["command"] for e in commands] == ["dig corp.internal"]


def test_event_appended_announces_other_events(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    client, _, session_id, lab_id = wired

    with client.websocket_connect(f"/labs/{lab_id}/terminal") as ws:
        _ready(ws)
        client.post(
            f"/sessions/{session_id}/events",
            json={
                "event_type": "content.opened",
                "event_version": 1,
                "occurred_at": NOW,
                "idempotency_key": "web:3",
                "attempt_id": None,
                "payload": {
                    "content_id": "concept.resolver",
                    "content_version": "1",
                    "pane": "side",
                    "source_highlight_id": None,
                },
            },
        )
        appended = _receive_until(ws, "event.appended", limit=40)

    assert appended["payload"]["event_type"] == "content.opened"
    assert appended["payload"]["position"] >= 1


def test_reset_announces_itself_and_closes_the_terminal(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    client, _, _, lab_id = wired

    with client.websocket_connect(f"/labs/{lab_id}/terminal") as ws:
        _ready(ws)
        replacement = client.post(f"/labs/{lab_id}/reset", json={"idempotency_key": "web:9"})
        assert replacement.status_code == 202
        resetting = _receive_until(ws, "lab.status", limit=40)
        while resetting["payload"]["status"] != "resetting":
            resetting = _receive_until(ws, "lab.status", limit=40)

    body = replacement.json()
    assert body["lab_instance_id"] != lab_id
    assert body["terminal_path"] == f"/labs/{body['lab_instance_id']}/terminal"


def test_terminal_cannot_be_reopened_after_a_restart(
    wired: tuple[TestClient, LoopFixture, str, str],
) -> None:
    """Known gap: ``LabInfo.runtime_ref`` is in no event and no projection, so a
    process that did not start the lab cannot attach a PTY to it. The connection
    is accepted and reports ``lab-unavailable`` rather than pretending."""
    _, fixture, _, lab_id = wired
    restarted = LearningLoop(
        store=fixture.store,
        schemas=fixture.schemas,
        adapters=fixture.adapters,
        algorithms=v01_algorithm_registry(),
        llm=fixture.llm,
        labs=fixture.labs,
        terminals=fixture.terminals,
        harness_version="0.1.0-test",
    )
    app = create_app(Backend(loop=restarted, store=fixture.store))

    with TestClient(app) as client, client.websocket_connect(f"/labs/{lab_id}/terminal") as ws:
        _receive_until(ws, "lab.status")
        error = _receive_until(ws, "error")
        failed = _receive_until(ws, "lab.status")

    assert error["payload"]["code"] == "lab-unavailable"
    assert failed["payload"]["status"] == "error"
