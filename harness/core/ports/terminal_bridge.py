"""Terminal bridge (PTY) Port (ADR-0015; ``contracts/schemas/ws/README.md``).

Opens an interactive PTY session inside a running lab instance and streams
raw bytes both ways. The PTY adapter lives in ``harness/adapters/pty/``.

The Port moves bytes only. Everything with meaning is done by its caller:
splitting output into ``terminal.output`` events and choosing ``utf-8`` or
``base64`` per chunk (a chunk may end mid code point), detecting when a
command is complete and appending ``terminal.command`` with its
``idempotency_key`` (ADR-0008), and forwarding chunks over the WebSocket.
Terminal input is untrusted and only ever written to the PTY inside the lab,
never to a host shell (``docs/ARCHITECTURE.md`` Security boundaries).

Asyncio-based, unlike the other Ports: a terminal is a long-lived duplex
stream served from the WebSocket handler's event loop.

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from harness.core.ports.lab_runtime import Argv, check_argv


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalSize:
    """PTY window size in character cells (xterm.js ``cols`` / ``rows``)."""

    cols: int
    rows: int

    def __post_init__(self) -> None:
        if self.cols <= 0 or self.rows <= 0:
            raise ValueError(f"cols and rows must be positive, got {self.cols}x{self.rows}")


@dataclass(frozen=True, slots=True, kw_only=True)
class TerminalOpenRequest:
    """What to open: which lab, which terminal id, which program.

    ``runtime_ref`` is the opaque handle from ``LabInfo.runtime_ref``; the bridge
    adapter paired with the lab runtime adapter by wiring interprets it.
    ``terminal_id`` is assigned by core (ADR-0016). ``argv`` is the program run
    on the PTY inside the lab (typically a login shell); it never runs on the
    host.
    """

    lab_instance_id: str
    runtime_ref: str
    terminal_id: str
    argv: Argv
    size: TerminalSize
    env: Mapping[str, str]
    workdir: str | None

    def __post_init__(self) -> None:
        check_argv(self.argv)


class TerminalSession(Protocol):
    """One open PTY. Owned by a single task; methods are not safe to call
    concurrently from several tasks, except that :meth:`output` may be consumed
    by one task while another writes."""

    @property
    def terminal_id(self) -> str:
        """The id given in :class:`TerminalOpenRequest`."""
        ...

    async def write(self, data: bytes) -> None:
        """Write input bytes to the PTY. Raises ``TerminalClosedError`` once closed."""
        ...

    async def resize(self, size: TerminalSize) -> None:
        """Change the PTY window size."""
        ...

    def output(self) -> AsyncIterator[bytes]:
        """Iterate output chunks as they arrive, in order, until the PTY process
        exits or the session is closed. Chunks are raw bytes of any length and
        may split multi-byte characters. Call at most once per session."""
        ...

    async def wait(self) -> int | None:
        """Wait for the PTY process to end; return its exit code, or ``None`` if
        it was killed or the session was closed before it exited."""
        ...

    async def close(self) -> None:
        """Terminate the PTY process and release resources. Idempotent. Ends
        :meth:`output`. Does not affect the lab instance itself."""
        ...


class TerminalBridgeError(Exception):
    """Base class for terminal bridge failures raised by adapters."""


class TerminalClosedError(TerminalBridgeError):
    """The session is closed or its process has exited."""


class TerminalBridge(Protocol):
    """Opens PTY sessions inside lab instances."""

    async def open(self, request: TerminalOpenRequest) -> TerminalSession:
        """Start ``request.argv`` on a new PTY inside the lab and return the session.

        Raises :class:`TerminalBridgeError` if the lab is not running or the
        program cannot be started.
        """
        ...
