"""Terminal session handling: PTY bytes in, events out (AC-B1, AC-B2, AC-B3).

The terminal bridge Port moves bytes only; everything with meaning happens here
(``harness/core/ports/terminal_bridge.py``):

- learner input is written to the PTY inside the lab and fed to the domain
  adapter's command detector; a completed command becomes one
  ``terminal.command`` event with a deterministic ``idempotency_key``;
- PTY output is split into ``terminal.output`` events, one per chunk, each
  chunk encoded ``utf-8`` when it decodes cleanly and holds no NUL, else
  ``base64`` (``contracts/schemas/websocket/payloads/terminal.output.json``);
- ``sequence`` is shared by commands and output chunks of one terminal and is
  assigned in append order.

This is the only asynchronous part of the loop. The event store is
synchronous, so appends run in a worker thread; one lock keeps sequence
numbers and append order identical even when input and output are handled by
two tasks.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from harness.core.domain_adapter import CommandDetector, DetectedCommand
from harness.core.loop.appender import EventAppender, EventDraft
from harness.core.loop.ids import Clock
from harness.core.loop.redaction import strip_nul
from harness.core.ports import (
    EventStore,
    JsonObject,
    StoredEvent,
    TerminalSession,
    TerminalSize,
)


def encode_chunk(data: bytes) -> tuple[str, str]:
    """``(data, encoding)`` for a ``terminal.output`` payload."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii"), "base64"
    if "\x00" in text:
        return base64.b64encode(data).decode("ascii"), "base64"
    return text, "utf-8"


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalChunk:
    """One output chunk, ready both for the WebSocket and as a stored event."""

    data: str
    encoding: str
    sequence: int
    event: StoredEvent

    def to_ws_payload(self) -> dict[str, str | int]:
        """``websocket/payloads/terminal.output.json``."""
        return {"data": self.data, "encoding": self.encoding, "sequence": self.sequence}


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalContext:
    """Everything the connection needs to write events for one terminal."""

    terminal_id: str
    lab_instance_id: str
    learner_id: str
    session_id: str
    attempt_id: str
    activity_definition_id: str


class TerminalConnection:
    """One open PTY plus its event recording."""

    def __init__(
        self,
        *,
        session: TerminalSession,
        context: TerminalContext,
        detector: CommandDetector,
        store: EventStore,
        appender: EventAppender,
        now: Clock,
    ) -> None:
        self._session = session
        self._context = context
        self._detector = detector
        self._store = store
        self._appender = appender
        self._now = now
        self._lock = asyncio.Lock()
        self._sequence = 0

    @property
    def terminal_id(self) -> str:
        return self._context.terminal_id

    @property
    def lab_instance_id(self) -> str:
        return self._context.lab_instance_id

    async def send_input(self, data: bytes) -> Sequence[StoredEvent]:
        """Write learner keystrokes to the PTY; return the commands they completed."""
        await self._session.write(data)
        return await self._record_commands(self._detector.feed_input(data))

    async def resize(self, size: TerminalSize) -> None:
        await self._session.resize(size)

    async def stream_output(self) -> AsyncIterator[TerminalChunk]:
        """Record and yield every output chunk until the PTY ends."""
        async for data in self._session.output():
            chunk = await self._record_output(data)
            await self._record_commands(self._detector.feed_output(data))
            yield chunk

    async def wait(self) -> int | None:
        return await self._session.wait()

    async def close(self) -> None:
        await self._session.close()

    # --- event writing -----------------------------------------------------

    def _draft(self, event_type: str, payload: JsonObject, occurred_at: datetime) -> EventDraft:
        context = self._context
        sequence = payload["sequence"]
        assert isinstance(sequence, int)
        return EventDraft(
            event_type=event_type,
            event_version=1,
            actor="learner" if event_type == "terminal.command" else "system",
            learner_id=context.learner_id,
            session_id=context.session_id,
            attempt_id=context.attempt_id,
            activity_definition_id=context.activity_definition_id,
            payload=payload,
            occurred_at=occurred_at,
            idempotency_key=f"term:{context.terminal_id}:{event_type}:{sequence}",
            causation_id=None,
            correlation_id=context.attempt_id,
        )

    def _append(self, draft: EventDraft) -> StoredEvent:
        with self._store.transaction() as tx:
            return self._appender.append(tx, draft).event

    async def _record_output(self, data: bytes) -> TerminalChunk:
        text, encoding = encode_chunk(data)
        async with self._lock:
            sequence = self._sequence
            self._sequence += 1
            draft = self._draft(
                "terminal.output",
                {
                    "terminal_id": self._context.terminal_id,
                    "lab_instance_id": self._context.lab_instance_id,
                    "sequence": sequence,
                    "data": text,
                    "encoding": encoding,
                },
                self._now(),
            )
            event = await asyncio.to_thread(self._append, draft)
        return TerminalChunk(data=text, encoding=encoding, sequence=sequence, event=event)

    async def _record_commands(self, commands: Sequence[DetectedCommand]) -> Sequence[StoredEvent]:
        events: list[StoredEvent] = []
        for command in commands:
            text = strip_nul(command.command)
            if not text:
                continue
            async with self._lock:
                sequence = self._sequence
                self._sequence += 1
                draft = self._draft(
                    "terminal.command",
                    {
                        "terminal_id": self._context.terminal_id,
                        "lab_instance_id": self._context.lab_instance_id,
                        "sequence": sequence,
                        "command": text,
                        "cwd": None if command.cwd is None else strip_nul(command.cwd),
                    },
                    self._now(),
                )
                events.append(await asyncio.to_thread(self._append, draft))
        return events
