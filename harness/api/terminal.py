"""The lab terminal WebSocket (``contracts/schemas/websocket/``, AC-B1, AC-B3, AC-C3).

One connection per LabInstance, at ``/labs/{lab_instance_id}/terminal``. The
routing here is as thin as the HTTP side: every byte and every event is handled
by :class:`~harness.core.loop.terminal.TerminalConnection`; this module only
frames messages, pumps them in both directions and pushes the three server
notifications the contract adds on top of the PTY stream:

- ``lab.status`` -- on connect, when the terminal is ready, and when a reset
  replaces this instance (pushed by ``POST /labs/{id}/reset`` through
  :class:`TerminalRegistry`);
- ``event.appended`` -- a thin notice for every other event appended to the
  session, read from the timeline, so a client knows when to refetch;
- ``error`` / ``pong`` -- connection-level replies.

Core's loop is synchronous, so every call into it runs in a worker thread
(``anyio.to_thread``); only :meth:`LearningLoop.open_terminal` and the
connection itself are asynchronous.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from harness.api.backend import Backend
from harness.core.loop import LoopError, TerminalConnection, problem_body
from harness.core.ports import PlainJson, TerminalSize

PROTOCOL_VERSION = 1
"""``websocket/envelope/fields.json``: one version for the whole message catalog."""

INITIAL_SIZE = TerminalSize(cols=80, rows=24)
"""The PTY is opened at this size; the client's first ``terminal.resize`` wins."""

EVENT_POLL_SECONDS = 0.5
# ponytail: `event.appended` polls the timeline because core has no append
# hook. One query per connection per interval is fine for a single local
# learner; give the appender a listener when the latency or the load matters.

logger = logging.getLogger(__name__)

router = APIRouter()


class Channel:
    """A WebSocket that only ever sends contract envelopes, one at a time."""

    def __init__(self, socket: WebSocket, *, correlation_id: str | None = None) -> None:
        self._socket = socket
        self._lock = anyio.Lock()
        self.correlation_id = correlation_id

    async def send(
        self,
        message_type: str,
        payload: Mapping[str, PlainJson],
        *,
        correlation_id: str | None = None,
    ) -> None:
        envelope: dict[str, PlainJson] = {
            "type": message_type,
            "protocol_version": PROTOCOL_VERSION,
            "correlation_id": self.correlation_id if correlation_id is None else correlation_id,
            "idempotency_key": None,
            "payload": dict(payload),
        }
        async with self._lock:
            if self._socket.client_state is not WebSocketState.CONNECTED:
                return
            await self._socket.send_json(envelope)

    async def send_error(self, code: str, message: str) -> None:
        await self.send("error", {"code": code, "message": message})

    async def close(self) -> None:
        async with self._lock:
            if self._socket.client_state is WebSocketState.CONNECTED:
                await self._socket.close()


class TerminalRegistry:
    """The open terminal channels, so a reset can announce itself (``websocket/README.md``)."""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def add(self, lab_instance_id: str, channel: Channel) -> None:
        self._channels[lab_instance_id] = channel

    def discard(self, lab_instance_id: str, channel: Channel) -> None:
        if self._channels.get(lab_instance_id) is channel:
            del self._channels[lab_instance_id]

    async def announce_reset(self, lab_instance_id: str) -> None:
        """Tell the replaced instance's terminal it is going away, and close it."""
        channel = self._channels.pop(lab_instance_id, None)
        if channel is None:
            return
        await channel.send(
            "lab.status",
            {"lab_instance_id": lab_instance_id, "status": "resetting", "terminal_id": None},
        )
        await channel.close()


def registry_of(app: Any) -> TerminalRegistry:
    """The registry stored on the application state by ``create_app``."""
    result = app.state.terminals
    assert isinstance(result, TerminalRegistry)
    return result


@router.websocket("/labs/{lab_instance_id}/terminal")
async def lab_terminal(websocket: WebSocket, lab_instance_id: str) -> None:
    """Bridge one lab's PTY and push the session's other events."""
    backend = websocket.app.state.backend
    assert isinstance(backend, Backend)
    loop = backend.loop
    # Raised before `accept()`, so an unknown lab is refused at the handshake
    # with a 404 problem document (`x-websockets` in the OpenAPI document).
    lab = await anyio.to_thread.run_sync(loop.lab_state, lab_instance_id)
    attempt = await anyio.to_thread.run_sync(loop.attempt_state, lab.attempt_id)

    await websocket.accept()
    channel = Channel(websocket, correlation_id=lab.attempt_id)
    await channel.send(
        "lab.status",
        {
            "lab_instance_id": lab_instance_id,
            "status": lab.status,
            "terminal_id": lab.terminal_id,
        },
    )

    try:
        connection = await loop.open_terminal(lab_instance_id=lab_instance_id, size=INITIAL_SIZE)
    except LoopError as error:
        body = problem_body(error)
        await channel.send_error(error.code, error.detail)
        await channel.send(
            "lab.status",
            {
                "lab_instance_id": lab_instance_id,
                "status": "error",
                "terminal_id": None,
                "reason": str(body["detail"]),
            },
        )
        await channel.close()
        return

    await channel.send(
        "lab.status",
        {
            "lab_instance_id": lab_instance_id,
            "status": "ready",
            "terminal_id": connection.terminal_id,
        },
    )
    registry_of(websocket.app).add(lab_instance_id, channel)
    cursor = (await anyio.to_thread.run_sync(loop.session_state, attempt.session_id)).last_position
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_pump_output, channel, connection)
            tasks.start_soon(_pump_events, channel, backend, attempt.session_id, cursor)
            await _read_client(channel, websocket, connection)
            tasks.cancel_scope.cancel()
    finally:
        registry_of(websocket.app).discard(lab_instance_id, channel)
        await connection.close()
        await channel.close()


async def _read_client(
    channel: Channel, websocket: WebSocket, connection: TerminalConnection
) -> None:
    """Client -> server: ``terminal.input``, ``terminal.resize``, ``ping``."""
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError, ValueError):
            return
        if not isinstance(message, dict):
            await channel.send_error("invalid-message", "a message must be a JSON object")
            continue
        message_type = message.get("type")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            await channel.send_error("invalid-message", "a message must carry a payload object")
            continue
        correlation = message.get("correlation_id")
        try:
            if message_type == "terminal.input":
                await connection.send_input(str(payload["data"]).encode())
            elif message_type == "terminal.resize":
                await connection.resize(
                    TerminalSize(cols=int(payload["cols"]), rows=int(payload["rows"]))
                )
            elif message_type == "ping":
                await channel.send(
                    "pong", {}, correlation_id=correlation if isinstance(correlation, str) else None
                )
            else:
                await channel.send_error("unknown-type", f"unsupported message type {message_type}")
        except (KeyError, TypeError, ValueError) as exc:
            await channel.send_error("invalid-message", f"{message_type}: {exc}")
        except LoopError as exc:
            await channel.send_error(exc.code, exc.detail)


async def _pump_output(channel: Channel, connection: TerminalConnection) -> None:
    """Server -> client: every recorded PTY chunk, then ``terminal.exit``."""
    async for chunk in connection.stream_output():
        await channel.send("terminal.output", dict(chunk.to_ws_payload()))
    exit_code = await connection.wait()
    await channel.send("terminal.exit", {"exit_code": exit_code, "signal": None})


async def _pump_events(
    channel: Channel, backend: Backend, session_id: str, after_position: int
) -> None:
    """Server -> client: a thin ``event.appended`` for every non-output event."""
    cursor = after_position
    while True:
        await anyio.sleep(EVENT_POLL_SECONDS)
        try:
            page = await anyio.to_thread.run_sync(
                partial(
                    backend.loop.session_timeline,
                    session_id,
                    after_position=cursor,
                    limit=500,
                )
            )
        except LoopError as exc:  # pragma: no cover - the session cannot vanish
            logger.warning("timeline poll failed for %s: %s", session_id, exc.detail)
            return
        for event in page.events:
            if event.event_type == "terminal.output":
                continue
            await channel.send(
                "event.appended",
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "event_version": event.event_version,
                    "position": event.position,
                    "occurred_at": event.to_dict()["occurred_at"],
                    "attempt_id": event.attempt_id,
                    "activity_definition_id": event.activity_definition_id,
                },
            )
        cursor = page.last_position
