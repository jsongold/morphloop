"""The lab artifact terminal records artifact.input / artifact.output (#62)."""

from __future__ import annotations

import asyncio
import base64

from lab_fixture import USER_ID, WS_ID, build, start

from harness.sdk import TerminalSize
from swe.artifacts.lab.terminal import encode_chunk


def test_encode_chunk() -> None:
    assert encode_chunk("é".encode()) == ("é", "utf-8")
    split = "é".encode()[:1]
    assert encode_chunk(split) == (base64.b64encode(split).decode(), "base64")
    assert encode_chunk(b"a\x00")[1] == "base64"


def test_terminal_records_commands_and_output() -> None:
    f = build()
    artifact_id = start(f)

    async def run() -> list[dict[str, str | int]]:
        size = TerminalSize(cols=80, rows=24)
        terminal = await f.service.open_terminal(artifact_id, size, user_id=USER_ID)
        session = f.terminals.sessions[0]
        assert session.request.runtime_ref.startswith("fake-lab_")
        chunks = []
        await terminal.send_input(b"dig example\n")  # the fake PTY echoes input as output
        async for chunk in terminal.stream_output():
            chunks.append(chunk)
            session.finish(0)
        assert await terminal.wait() == 0
        return chunks

    chunks = asyncio.run(run())
    assert chunks == [{"data": "dig example\n", "encoding": "utf-8", "sequence": 1}]
    io = [e for e in f.store.read(ws_id=WS_ID) if e.type in ("artifact.input", "artifact.output")]
    assert [(e.type, e.actor, e.payload["labels"], e.payload["sequence"]) for e in io] == [
        ("artifact.input", "learner", ["io:command"], 0),
        ("artifact.output", "system", ["io:output"], 1),
    ]
    assert io[0].payload["data"] == "dig example"
