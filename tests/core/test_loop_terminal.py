"""The terminal connection: bytes in, ``terminal.command`` / ``terminal.output`` out.

AC-B1 (terminal in an isolated lab), AC-B2 (every command persisted with its
session and attempt), AC-B3 (output is on the timeline).
"""

from __future__ import annotations

import base64

import pytest
from loop_harness import ACTIVITY_ID, LEARNER_ID, LoopFixture, build_loop

from harness.core.loop import LabUnavailableError, encode_chunk
from harness.core.ports import TerminalSize

SIZE = TerminalSize(cols=80, rows=24)


def started(fixture: LoopFixture) -> tuple[str, str, str]:
    session = fixture.loop.start_session(
        learner_id=LEARNER_ID, pack=fixture.pack, idempotency_key="web:1"
    )
    session_id = session.session.session_id
    attempt = fixture.loop.start_attempt(
        session_id=session_id, activity_definition_id=ACTIVITY_ID, idempotency_key="web:2"
    )
    assert attempt.lab is not None
    return session_id, attempt.attempt_id, attempt.lab.lab_instance_id


def test_encode_chunk_falls_back_to_base64() -> None:
    assert encode_chunk(b"hello") == ("hello", "utf-8")
    assert encode_chunk("héllo".encode()) == ("héllo", "utf-8")
    split = "é".encode()[:1]
    assert encode_chunk(split) == (base64.b64encode(split).decode(), "base64")
    assert encode_chunk(b"a\x00b") == (base64.b64encode(b"a\x00b").decode(), "base64")


@pytest.mark.asyncio
async def test_input_becomes_a_command_event_and_output_becomes_chunks() -> None:
    fixture = build_loop()
    session_id, attempt_id, lab_instance_id = started(fixture)

    connection = await fixture.loop.open_terminal(lab_instance_id=lab_instance_id, size=SIZE)
    assert fixture.loop.lab_state(lab_instance_id).terminal_id == connection.terminal_id

    commands = await connection.send_input(b"dig api.corp.internal\n")
    assert [event.payload["command"] for event in commands] == ["dig api.corp.internal"]
    command_event = commands[0]
    assert command_event.session_id == session_id
    assert command_event.attempt_id == attempt_id
    assert command_event.activity_definition_id == ACTIVITY_ID
    assert command_event.payload["lab_instance_id"] == lab_instance_id
    assert command_event.actor == "learner"
    assert command_event.idempotency_key is not None

    # The fake PTY echoes what was written, so one chunk is waiting.
    chunks = []
    async for chunk in connection.stream_output():
        chunks.append(chunk)
        await connection.close()
    assert [chunk.data for chunk in chunks] == ["dig api.corp.internal\n"]
    assert chunks[0].to_ws_payload() == {
        "data": "dig api.corp.internal\n",
        "encoding": "utf-8",
        "sequence": chunks[0].sequence,
    }
    output_event = chunks[0].event
    assert output_event.event_type == "terminal.output"
    assert output_event.actor == "system"
    assert output_event.payload["terminal_id"] == connection.terminal_id

    timeline = fixture.loop.session_timeline(session_id)
    types = [event.event_type for event in timeline.events]
    assert "terminal.command" in types and "terminal.output" in types
    sequences = [
        event.payload["sequence"]
        for event in timeline.events
        if event.event_type in ("terminal.command", "terminal.output")
    ]
    assert sequences == sorted(sequences)


@pytest.mark.asyncio
async def test_binary_output_is_stored_as_base64() -> None:
    fixture = build_loop()
    _, _, lab_instance_id = started(fixture)
    connection = await fixture.loop.open_terminal(lab_instance_id=lab_instance_id, size=SIZE)
    session = fixture.terminals.sessions[-1]
    session.push_output(b"\xff\xfe binary")

    chunks = []
    async for chunk in connection.stream_output():
        chunks.append(chunk)
        await connection.close()
    assert chunks[0].encoding == "base64"
    assert base64.b64decode(chunks[0].data) == b"\xff\xfe binary"
    assert chunks[0].event.payload["encoding"] == "base64"


@pytest.mark.asyncio
async def test_a_terminal_reattaches_from_the_rebuilt_projection() -> None:
    fixture = build_loop()
    _, _, lab_instance_id = started(fixture)
    fixture.loop.rebuild()  # the projections survive a restart, the handles do not
    fixture.loop._handles.clear()
    await fixture.loop.open_terminal(lab_instance_id=lab_instance_id, size=SIZE)
    assert fixture.terminals.sessions[-1].request.runtime_ref == f"fake-{lab_instance_id}"


@pytest.mark.asyncio
async def test_a_replaced_lab_cannot_open_a_terminal() -> None:
    fixture = build_loop()
    _, _, lab_instance_id = started(fixture)
    fixture.loop.reset_lab(lab_instance_id=lab_instance_id, idempotency_key="web:reset")
    with pytest.raises(LabUnavailableError):
        await fixture.loop.open_terminal(lab_instance_id=lab_instance_id, size=SIZE)
