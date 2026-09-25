"""A PTY inside a lab artifact, recorded as ``artifact.input`` / ``artifact.output`` (#62).

Same job as the v0.1 ``harness.core.loop.terminal`` but with generic event types
labelled ``io:command`` / ``io:output``: output chunks are recorded one event
each (``utf-8`` when clean, else ``base64``); input bytes go to the PTY and to
the tool's command detector, and each completed command is one event.
``sequence`` is shared by both and assigned in append order under one lock.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Literal

from harness.core.domain_adapter import CommandDetector, DetectedCommand
from harness.core.ports.events_v2 import StoredEventV2
from harness.core.ports.json_types import JsonObject, PlainJson
from harness.core.ports.terminal_bridge import TerminalSession, TerminalSize

type Append = Callable[[str, str, Literal["learner", "system"], JsonObject], StoredEventV2]
"""``(event_id, type, actor, payload) -> stored event``, scoped by the caller."""


def encode_chunk(data: bytes) -> tuple[str, str]:
    """``(data, encoding)``: ``utf-8`` if it decodes cleanly without NUL, else ``base64``."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = "\x00"
    if "\x00" in text:
        return base64.b64encode(data).decode("ascii"), "base64"
    return text, "utf-8"


class LabTerminal:
    """One open PTY plus its event recording."""

    def __init__(
        self,
        *,
        artifact_id: str,
        lab_instance_id: str,
        session: TerminalSession,
        detector: CommandDetector,
        append: Append,
    ) -> None:
        self.artifact_id = artifact_id
        self.lab_instance_id = lab_instance_id
        self._session = session
        self._detector = detector
        self._append = append
        self._lock = asyncio.Lock()
        self._sequence = 0

    @property
    def terminal_id(self) -> str:
        return self._session.terminal_id

    async def send_input(self, data: bytes) -> None:
        await self._session.write(data)
        await self._record_commands(self._detector.feed_input(data))

    async def resize(self, size: TerminalSize) -> None:
        await self._session.resize(size)

    async def stream_output(self) -> AsyncIterator[dict[str, str | int]]:
        """Record every output chunk and yield it as a ``terminal.output`` WS payload."""
        async for data in self._session.output():
            text, encoding = encode_chunk(data)
            sequence = await self._record("system", ["io:output"], text, encoding, None)
            await self._record_commands(self._detector.feed_output(data))
            yield {"data": text, "encoding": encoding, "sequence": sequence}

    async def wait(self) -> int | None:
        return await self._session.wait()

    async def close(self) -> None:
        await self._session.close()

    async def _record_commands(self, commands: Sequence[DetectedCommand]) -> None:
        for command in commands:
            text = command.command.replace("\x00", "")
            if text:
                cwd = None if command.cwd is None else command.cwd.replace("\x00", "")
                await self._record("learner", ["io:command"], text, "utf-8", cwd)

    async def _record(
        self,
        actor: Literal["learner", "system"],
        labels: list[PlainJson],
        data: str,
        encoding: str,
        cwd: str | None,
    ) -> int:
        async with self._lock:
            sequence = self._sequence
            self._sequence += 1
            payload: dict[str, PlainJson] = {
                "artifact_id": self.artifact_id,
                "labels": labels,
                "sequence": sequence,
                "data": data,
                "encoding": encoding,
            }
            if cwd:
                payload["attrs"] = {"cwd": cwd}
            event_type = "artifact.input" if actor == "learner" else "artifact.output"
            # Deterministic id: a retried append of the same chunk is the same event.
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.terminal_id}:{sequence}"))
            await asyncio.to_thread(self._append, event_id, event_type, actor, payload)
        return sequence
