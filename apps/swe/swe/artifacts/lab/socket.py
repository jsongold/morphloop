"""`/v2/artifacts/{artifact_id}/terminal` (#62, #95): the lab artifact's terminal WebSocket.

Speaks the SDK's `contracts/schemas/websocket/` messages: client
`terminal.input` / `terminal.resize` / `ping`; server `lab.status` (ready or
error), `terminal.output`, `terminal.exit`, `error`, `pong`. Recording is done by
:class:`~swe.artifacts.lab.LabTerminal` (`artifact.input` / `artifact.output`);
this module only frames and pumps messages. The learner comes from `UserIdDep`,
like every HTTP route.
"""

from __future__ import annotations

from collections.abc import Mapping

import anyio
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from harness.sdk import PlainJson, TerminalBridgeError, TerminalSize, UserIdDep
from swe.artifacts.lab.routes import artifact_service_of
from swe.artifacts.lab.service import ArtifactError
from swe.artifacts.lab.terminal import LabTerminal

PROTOCOL_VERSION = 1
INITIAL_SIZE = TerminalSize(cols=80, rows=24)
"""The PTY opens at this size; the client's first `terminal.resize` wins."""

router = APIRouter()


class _Channel:
    """Sends contract envelopes one at a time."""

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._lock = anyio.Lock()

    async def send(
        self, message_type: str, payload: Mapping[str, PlainJson], correlation: str | None = None
    ) -> None:
        async with self._lock:
            if self._socket.client_state is WebSocketState.CONNECTED:
                await self._socket.send_json(
                    {
                        "type": message_type,
                        "protocol_version": PROTOCOL_VERSION,
                        "correlation_id": correlation,
                        "idempotency_key": None,
                        "payload": dict(payload),
                    }
                )


@router.websocket("/artifacts/{artifact_id}/terminal")
async def artifact_terminal(websocket: WebSocket, artifact_id: str, user_id: UserIdDep) -> None:
    await websocket.accept()
    channel = _Channel(websocket)
    try:
        service = artifact_service_of(websocket)
        terminal = await service.open_terminal(artifact_id, INITIAL_SIZE, user_id=user_id)
    except ArtifactError as error:
        await channel.send("error", {"code": error.code, "message": error.detail})
        await websocket.close()
        return
    await channel.send(
        "lab.status",
        {
            "lab_instance_id": terminal.lab_instance_id,
            "status": "ready",
            "terminal_id": terminal.terminal_id,
        },
    )
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_pump_output, channel, terminal)
            await _read_client(channel, websocket, terminal)
            tasks.cancel_scope.cancel()
    finally:
        await terminal.close()
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()


async def _read_client(channel: _Channel, websocket: WebSocket, terminal: LabTerminal) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError, ValueError):
            return
        payload = message.get("payload") if isinstance(message, dict) else None
        if not isinstance(payload, dict):
            await channel.send("error", {"code": "invalid-message", "message": "bad message"})
            continue
        message_type = message.get("type")
        correlation = message.get("correlation_id")
        try:
            if message_type == "terminal.input":
                await terminal.send_input(str(payload["data"]).encode())
            elif message_type == "terminal.resize":
                await terminal.resize(TerminalSize(cols=payload["cols"], rows=payload["rows"]))
            elif message_type == "ping":
                await channel.send(
                    "pong", {}, correlation if isinstance(correlation, str) else None
                )
            else:
                await channel.send(
                    "error", {"code": "unknown-type", "message": f"unsupported {message_type}"}
                )
        except (KeyError, TypeError, ValueError, TerminalBridgeError) as exc:
            await channel.send("error", {"code": "invalid-message", "message": str(exc)})


async def _pump_output(channel: _Channel, terminal: LabTerminal) -> None:
    async for chunk in terminal.stream_output():
        await channel.send("terminal.output", chunk)
    await channel.send("terminal.exit", {"exit_code": await terminal.wait(), "signal": None})
