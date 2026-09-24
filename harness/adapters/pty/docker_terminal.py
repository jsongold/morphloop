"""TerminalBridge adapter: an interactive PTY inside a running Docker lab container.

``runtime_ref`` is the lab container's id (or name), as produced by the Docker
lab runtime adapter. It is treated as opaque and only ever passed to the Docker
API; this module does not import any other adapter (``.importlinter``).

Security (``docs/ARCHITECTURE.md`` Security boundaries): ``request.argv`` runs
through ``docker exec`` inside the lab container only. Nothing here runs a host
shell, spawns a host process or allocates a host PTY; terminal input is written
to the exec's attached stream and nowhere else.

Transport. ``aiodocker`` talks to the daemon on the event loop: the exec is
attached with :meth:`aiodocker.execs.Exec.start`, whose stream has no read
timeout, so an idle shell stays open however long it waits for input. One
reader task per session copies output chunks into a queue until EOF (process
exit or close), so :meth:`DockerTerminalSession.wait` works whether or not
anyone consumes :meth:`DockerTerminalSession.output`.

Terminating the process. Docker has no "kill exec" API and closing the attach
stream does not end a TTY exec. The exec is therefore started with a random
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
from collections.abc import AsyncIterator

import aiodocker
import aiohttp
from aiodocker.containers import DockerContainer
from aiodocker.execs import Exec
from aiodocker.stream import Stream

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

_EXIT_POLL_INTERVAL = 0.05
_EXIT_POLL_ATTEMPTS = 100

# What the daemon connection raises: API errors and transport failures.
_DOCKER_ERRORS = (aiodocker.DockerError, aiohttp.ClientError, OSError)


class DockerTerminalSession:
    """One ``docker exec`` with a TTY, attached over an aiodocker stream."""

    def __init__(
        self,
        *,
        terminal_id: str,
        container: DockerContainer,
        exec_: Exec,
        marker: str,
        stream: Stream,
    ) -> None:
        self._terminal_id = terminal_id
        self._container = container
        self._exec = exec_
        self._marker = marker
        self._stream = stream
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._eof = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._exit_code: int | None = None
        self._output_taken = False
        self._reader = asyncio.create_task(self._read_loop(), name=f"pty-reader-{terminal_id}")

    @property
    def terminal_id(self) -> str:
        return self._terminal_id

    async def _read_loop(self) -> None:
        try:
            while (message := await self._stream.read_out()) is not None:
                self._queue.put_nowait(message.data)
        except (*_DOCKER_ERRORS, RuntimeError):
            pass
        finally:
            self._on_eof()

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
                await self._stream.write_in(data)
            except (*_DOCKER_ERRORS, RuntimeError) as exc:
                raise TerminalClosedError(self._terminal_id) from exc

    async def resize(self, size: TerminalSize) -> None:
        self._check_open()
        try:
            await self._exec.resize(h=size.rows, w=size.cols)
        except _DOCKER_ERRORS as exc:
            raise TerminalBridgeError(f"resize failed: {exc}") from exc

    async def output(self) -> AsyncIterator[bytes]:
        if self._output_taken:
            raise TerminalBridgeError("output() may be called at most once per session")
        self._output_taken = True
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def wait(self) -> int | None:
        await self._eof.wait()
        async with self._close_lock:
            if self._closed:
                return self._exit_code
        try:
            for _ in range(_EXIT_POLL_ATTEMPTS):
                info = await self._exec.inspect()
                if not info.get("Running"):
                    code = info.get("ExitCode")
                    return code if isinstance(code, int) else None
                await asyncio.sleep(_EXIT_POLL_INTERVAL)
        except _DOCKER_ERRORS:
            return None
        return None

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                info = await self._exec.inspect()
                if info.get("Running"):
                    await self._kill_in_lab()
                else:
                    code = info.get("ExitCode")
                    self._exit_code = code if isinstance(code, int) else None
            except _DOCKER_ERRORS:
                pass  # container gone: nothing left to terminate
            with contextlib.suppress(*_DOCKER_ERRORS):
                await self._stream.close()
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._on_eof()

    async def _kill_in_lab(self) -> None:
        with contextlib.suppress(*_DOCKER_ERRORS):
            killer = await self._container.exec(
                ["/bin/sh", "-c", _KILL_SCRIPT, "morphloop-pty-kill", self._marker],
                stdout=True,
                stderr=True,
            )
            async with killer.start() as stream:  # attached: returns when the helper ends
                while await stream.read_out() is not None:
                    pass


class DockerTerminalBridge:
    """:class:`~harness.core.ports.TerminalBridge` over ``docker exec`` with a TTY."""

    def __init__(self, client: aiodocker.Docker | None = None) -> None:
        self._client = client

    def _docker(self) -> aiodocker.Docker:
        # Created lazily so the aiohttp session belongs to the serving event loop.
        if self._client is None:
            self._client = aiodocker.Docker()
        return self._client

    async def open(self, request: TerminalOpenRequest) -> TerminalSession:
        marker = secrets.token_hex(16)
        try:
            docker = self._docker()
            container = await docker.containers.get(request.runtime_ref)
            if not (container["State"] or {}).get("Running"):
                raise TerminalBridgeError(f"lab {request.lab_instance_id!r} is not running")
            exec_ = await container.exec(
                list(request.argv),
                stdin=True,
                tty=True,
                environment={**request.env, MARKER_ENV: marker},
                workdir=request.workdir,
            )
            stream = exec_.start(timeout=None)  # no read timeout: idle shells stay open
            await stream.__aenter__()  # attach now so failures surface as open failures
        except aiodocker.DockerError as exc:
            if exc.status == 404:
                raise TerminalBridgeError(f"lab {request.lab_instance_id!r} not found") from exc
            raise TerminalBridgeError(f"cannot start terminal: {exc}") from exc
        except (aiohttp.ClientError, OSError, ValueError) as exc:
            raise TerminalBridgeError(f"Docker unavailable: {exc}") from exc

        session = DockerTerminalSession(
            terminal_id=request.terminal_id,
            container=container,
            exec_=exec_,
            marker=marker,
            stream=stream,
        )
        # A program that already exited cannot be resized; that is not an open failure.
        with contextlib.suppress(TerminalBridgeError):
            await session.resize(request.size)
        return session
