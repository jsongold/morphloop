"""TerminalBridge adapter: an interactive PTY inside a running Docker lab container.

``runtime_ref`` is the lab container's id (or name), as produced by the Docker
lab runtime adapter. It is treated as opaque and only ever passed to the Docker
API; this module does not import any other adapter (``.importlinter``).

Security (``docs/ARCHITECTURE.md`` Security boundaries): ``request.argv`` runs
through ``docker exec`` inside the lab container only. Nothing here runs a host
shell, spawns a host process or allocates a host PTY; terminal input is written
to the exec's attached socket and nowhere else.

Threading model. The Docker SDK is blocking, so:

* one-shot API calls (inspect, exec create/start/resize, the kill helper) run in
  the default executor via :func:`asyncio.to_thread`;
* output is read by one dedicated daemon thread per session doing blocking
  ``recv`` on the hijacked exec socket and handing each chunk to the event loop
  with ``call_soon_threadsafe``; EOF (process exit or close) ends the stream;
* writes are ``sendall`` calls in the executor, serialised by an
  :class:`asyncio.Lock` so chunks never interleave.

Terminating the process. Docker has no "kill exec" API and closing the attach
socket does not end a TTY exec. The exec is therefore started with a random
marker variable in its environment; :meth:`DockerTerminalSession.close` runs a
fixed helper inside the lab that sends ``SIGHUP`` (a terminal hang-up) and then
``SIGKILL`` to every process carrying that marker. The marker is passed as a
positional argument, never interpolated into the script. This is best effort:
it needs ``/bin/sh`` in the image, and a process that scrubs its environment
escapes it (the lab itself is disposable and is destroyed separately).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import socket
import threading
from collections.abc import AsyncIterator
from typing import Any

import docker
import docker.errors

from harness.core.ports.terminal_bridge import (
    TerminalBridgeError,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
)

__all__ = ["DockerTerminalBridge", "DockerTerminalSession"]

MARKER_ENV = "MORPHLOOP_PTY_SESSION"

# Runs inside the lab, never on the host. $1 is the marker value.
_KILL_SCRIPT = (
    'm="' + MARKER_ENV + '=$1"; '
    "hit() { tr '\\000' '\\n' < \"$1/environ\" 2>/dev/null | grep -qxF \"$m\"; }; "
    'for s in HUP KILL; do for p in /proc/[0-9]*; do [ "$p" = "/proc/$$" ] && continue; '
    'hit "$p" && kill -"$s" "${p#/proc/}" 2>/dev/null; done; sleep 0.2; done; true'
)

_READ_SIZE = 64 * 1024
_EXIT_POLL_INTERVAL = 0.05
_EXIT_POLL_ATTEMPTS = 100


def _raw_socket(attached: Any) -> socket.socket:
    """The plain socket under what ``exec_start(socket=True)`` returned."""
    if isinstance(attached, socket.socket):
        return attached
    inner = getattr(attached, "_sock", None)
    if isinstance(inner, socket.socket):
        return inner
    raise TerminalBridgeError(
        f"unsupported Docker transport for a terminal: {type(attached).__name__}"
    )


class DockerTerminalSession:
    """One ``docker exec`` with a TTY, attached over a hijacked socket."""

    def __init__(
        self,
        *,
        api: docker.APIClient,
        terminal_id: str,
        container: str,
        exec_id: str,
        marker: str,
        attached: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._api = api
        self._terminal_id = terminal_id
        self._container = container
        self._exec_id = exec_id
        self._marker = marker
        self._attached = attached  # keeps the HTTP response (and so the socket) alive
        self._sock = _raw_socket(attached)
        # The socket inherits the Docker client's request timeout (60s by
        # default), so an idle shell looked like EOF. Block until output or
        # close; _release_socket shuts it down to end the read.
        self._sock.settimeout(None)
        self._loop = loop
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._eof = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._exit_code: int | None = None
        self._output_taken = False
        self._reader = threading.Thread(
            target=self._read_loop, name=f"pty-reader-{terminal_id}", daemon=True
        )
        self._reader.start()

    @property
    def terminal_id(self) -> str:
        return self._terminal_id

    # --- reader thread ------------------------------------------------------

    def _read_loop(self) -> None:
        try:
            while True:
                data = self._sock.recv(_READ_SIZE)
                if not data:
                    break
                self._post(self._queue.put_nowait, data)
        except OSError:
            pass
        finally:
            self._post(self._on_eof)

    def _post(self, fn: Any, *args: Any) -> None:
        with contextlib.suppress(RuntimeError):  # event loop already closed
            self._loop.call_soon_threadsafe(fn, *args)

    def _on_eof(self) -> None:
        if not self._eof.is_set():
            self._eof.set()
            self._queue.put_nowait(None)

    # --- TerminalSession ----------------------------------------------------

    def _check_open(self) -> None:
        if self._closed or self._eof.is_set():
            raise TerminalClosedError(self._terminal_id)

    async def write(self, data: bytes) -> None:
        self._check_open()
        async with self._write_lock:
            self._check_open()
            try:
                await asyncio.to_thread(self._sock.sendall, data)
            except OSError as exc:
                raise TerminalClosedError(self._terminal_id) from exc

    async def resize(self, size: TerminalSize) -> None:
        self._check_open()
        try:
            await asyncio.to_thread(
                self._api.exec_resize, self._exec_id, height=size.rows, width=size.cols
            )
        except docker.errors.APIError as exc:
            raise TerminalBridgeError(f"resize failed: {exc}") from exc

    async def output(self) -> AsyncIterator[bytes]:
        if self._output_taken:
            raise TerminalBridgeError("output() may be called at most once per session")
        self._output_taken = True
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def _inspect(self) -> dict[str, Any]:
        info: dict[str, Any] = await asyncio.to_thread(self._api.exec_inspect, self._exec_id)
        return info

    async def wait(self) -> int | None:
        await self._eof.wait()
        async with self._close_lock:
            if self._closed:
                return self._exit_code
        try:
            for _ in range(_EXIT_POLL_ATTEMPTS):
                info = await self._inspect()
                if not info.get("Running"):
                    code = info.get("ExitCode")
                    return code if isinstance(code, int) else None
                await asyncio.sleep(_EXIT_POLL_INTERVAL)
        except docker.errors.APIError:
            return None
        return None

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                info = await self._inspect()
                if info.get("Running"):
                    await asyncio.to_thread(self._kill_in_lab)
                else:
                    code = info.get("ExitCode")
                    self._exit_code = code if isinstance(code, int) else None
            except docker.errors.APIError:
                pass  # container gone: nothing left to terminate
            self._release_socket()
            self._on_eof()
            await asyncio.to_thread(self._reader.join, 5.0)

    def _kill_in_lab(self) -> None:
        with contextlib.suppress(docker.errors.APIError):
            created = self._api.exec_create(
                self._container,
                ["/bin/sh", "-c", _KILL_SCRIPT, "morphloop-pty-kill", self._marker],
                stdout=True,
                stderr=True,
            )
            self._api.exec_start(created["Id"])

    def _release_socket(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._sock.close()
        response = getattr(self._attached, "_response", None)
        if response is not None:
            with contextlib.suppress(Exception):
                response.close()


class DockerTerminalBridge:
    """:class:`~harness.core.ports.TerminalBridge` over ``docker exec`` with a TTY."""

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client

    def _api(self) -> docker.APIClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client.api

    async def open(self, request: TerminalOpenRequest) -> TerminalSession:
        loop = asyncio.get_running_loop()
        api = await asyncio.to_thread(self._api)
        container = request.runtime_ref
        marker = secrets.token_hex(16)

        def start() -> tuple[str, Any]:
            state = api.inspect_container(container).get("State") or {}
            if not state.get("Running"):
                raise TerminalBridgeError(f"lab {request.lab_instance_id!r} is not running")
            env = dict(request.env)
            env[MARKER_ENV] = marker
            created = api.exec_create(
                container,
                list(request.argv),
                stdin=True,
                tty=True,
                environment=env,
                workdir=request.workdir,
            )
            exec_id: str = created["Id"]
            attached = api.exec_start(exec_id, tty=True, socket=True)
            _raw_socket(attached)
            return exec_id, attached

        try:
            exec_id, attached = await asyncio.to_thread(start)
        except docker.errors.NotFound as exc:
            raise TerminalBridgeError(f"lab {request.lab_instance_id!r} not found") from exc
        except docker.errors.APIError as exc:
            raise TerminalBridgeError(f"cannot start terminal: {exc}") from exc
        except docker.errors.DockerException as exc:
            raise TerminalBridgeError(f"Docker unavailable: {exc}") from exc

        session = DockerTerminalSession(
            api=api,
            terminal_id=request.terminal_id,
            container=container,
            exec_id=exec_id,
            marker=marker,
            attached=attached,
            loop=loop,
        )
        # A program that already exited cannot be resized; that is not an open failure.
        with contextlib.suppress(TerminalBridgeError):
            await session.resize(request.size)
        return session
