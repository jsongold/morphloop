"""In-memory fakes of the LLM, lab and terminal Ports (``harness.core.ports``) for tests.

Each fake implements its Port structurally and keeps just enough behaviour for
core and API tests to run without Postgres, an LLM provider, Docker or a PTY:

- :class:`FakeLLMProvider` -- returns scripted outputs (or raises scripted
  errors) and records every request.
- :class:`FakeLabRuntime` -- tracks lab lifecycle; command results come from a
  caller-supplied handler.
- :class:`FakeTerminalBridge` -- sessions that echo input to output.

It also holds a fake domain adapter (``harness.core.domain_adapter``):
:class:`FakeDomainAdapter` with :class:`FakeCommandExitCheck`,
:class:`FakeFixtureProvider` and :class:`FakeTerminalTool`.

This module may import ``harness.core``; core must never import it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from harness.core.domain_adapter import (
    AdapterParamsError,
    Check,
    CheckObservation,
    CommandDetector,
    DetectedCommand,
    DomainAdapter,
    FixtureProvider,
    TerminalLaunch,
    TerminalTool,
)
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    ImageRef,
    JsonObject,
    LabFile,
    LabInfo,
    LabNotFoundError,
    LabNotReadyError,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    LabStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    NetworkMode,
    ResourceLimits,
    TerminalBridge,
    TerminalBridgeError,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
    to_plain_object,
)

# --- LLM --------------------------------------------------------------------


class FakeLLMProvider:
    """:class:`~harness.core.ports.LLMProvider` returning scripted outputs in order.

    Each script item is an output object or an exception to raise. Provenance
    echoes ``request.llm``, as a real adapter reports what it sent.
    """

    def __init__(self, script: Sequence[JsonObject | Exception] = ()) -> None:
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def enqueue(self, item: JsonObject | Exception) -> None:
        self._script.append(item)

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("FakeLLMProvider: no scripted output left")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(output=item, provenance=request.llm)


# --- Lab runtime ------------------------------------------------------------


_READINESS_MAX_OUTPUT_BYTES = 4096


def _succeed(lab_instance_id: str, request: ExecRequest) -> ExecResult:
    return ExecResult(
        exit_code=0,
        stdout=b"",
        stderr=b"",
        timed_out=False,
        stdout_truncated=False,
        stderr_truncated=False,
    )


class FakeLabRuntime:
    """:class:`~harness.core.ports.LabRuntime` that tracks lifecycle only.

    ``exec_handler`` decides each command's result (default: exit 0, no output).
    ``exec_calls`` records ``(lab_instance_id, request)`` in call order.

    ``spec.command`` is only recorded (nothing runs in process). A
    ``spec.readiness`` probe is run once through ``exec_handler`` at the end
    of :meth:`start`, and :class:`LabNotReadyError` is raised unless it exits
    ``0``: no time passes in a fake, so retrying would only loop over the same
    answer. The probe appears in ``exec_calls`` like any other command.
    """

    def __init__(self, exec_handler: Callable[[str, ExecRequest], ExecResult] = _succeed) -> None:
        self.labs: dict[str, LabSpec] = {}
        self.exec_calls: list[tuple[str, ExecRequest]] = []
        self._exec_handler = exec_handler

    def start(self, lab_instance_id: str, spec: LabSpec) -> LabInfo:
        if lab_instance_id in self.labs:
            raise LabRuntimeError(f"lab {lab_instance_id!r} already exists")
        self.labs[lab_instance_id] = spec
        if spec.readiness is not None:
            probe = spec.readiness
            result = self.exec(
                lab_instance_id,
                ExecRequest(
                    argv=probe.argv,
                    timeout_seconds=probe.timeout_seconds,
                    max_output_bytes=_READINESS_MAX_OUTPUT_BYTES,
                    env={},
                    workdir=None,
                ),
            )
            if result.timed_out or result.exit_code != 0:
                del self.labs[lab_instance_id]
                raise LabNotReadyError(
                    f"lab {lab_instance_id!r} is not ready: {list(probe.argv)} exited "
                    f"{result.exit_code!r}"
                )
        return LabInfo(
            lab_instance_id=lab_instance_id,
            runtime_ref=f"fake-{lab_instance_id}",
            image_digest=spec.image.digest,
            status="running",
        )

    def reset(self, lab_instance_id: str, *, new_lab_instance_id: str, spec: LabSpec) -> LabInfo:
        self.destroy(lab_instance_id)
        return self.start(new_lab_instance_id, spec)

    def destroy(self, lab_instance_id: str) -> None:
        self.labs.pop(lab_instance_id, None)

    def exec(self, lab_instance_id: str, request: ExecRequest) -> ExecResult:
        if lab_instance_id not in self.labs:
            raise LabNotFoundError(lab_instance_id)
        self.exec_calls.append((lab_instance_id, request))
        return self._exec_handler(lab_instance_id, request)

    def status(self, lab_instance_id: str) -> LabStatus:
        return "running" if lab_instance_id in self.labs else "absent"


# --- Terminal bridge --------------------------------------------------------


class FakeTerminalSession:
    """A PTY stand-in that echoes every write back as output.

    ``push_output`` injects output as if a program printed it; ``finish``
    ends the session with an exit code.
    """

    def __init__(self, request: TerminalOpenRequest) -> None:
        self.request = request
        self.size = request.size
        self.written: list[bytes] = []
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False
        self._exit_code: int | None = None
        self._done = asyncio.Event()

    @property
    def terminal_id(self) -> str:
        return self.request.terminal_id

    def push_output(self, data: bytes) -> None:
        self._queue.put_nowait(data)

    def finish(self, exit_code: int | None) -> None:
        if not self._closed:
            self._exit_code = exit_code
            self._closed = True
            self._queue.put_nowait(None)
            self._done.set()

    async def write(self, data: bytes) -> None:
        if self._closed:
            raise TerminalClosedError(self.terminal_id)
        self.written.append(data)
        self._queue.put_nowait(data)

    async def resize(self, size: TerminalSize) -> None:
        if self._closed:
            raise TerminalClosedError(self.terminal_id)
        self.size = size

    async def output(self) -> AsyncIterator[bytes]:
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def wait(self) -> int | None:
        await self._done.wait()
        return self._exit_code

    async def close(self) -> None:
        self.finish(None)


class FakeTerminalBridge:
    """:class:`~harness.core.ports.TerminalBridge` opening :class:`FakeTerminalSession`\\ s.

    ``running_labs``, when given, is the set of lab ids that accept a terminal.
    """

    def __init__(self, running_labs: set[str] | None = None) -> None:
        self.sessions: list[FakeTerminalSession] = []
        self._running_labs = running_labs

    async def open(self, request: TerminalOpenRequest) -> TerminalSession:
        if self._running_labs is not None and request.lab_instance_id not in self._running_labs:
            raise TerminalBridgeError(f"lab {request.lab_instance_id!r} is not running")
        session = FakeTerminalSession(request)
        self.sessions.append(session)
        return session


def _check_conformance() -> None:  # pragma: no cover - evaluated by mypy only
    _llm: LLMProvider = FakeLLMProvider()
    _lab: LabRuntime = FakeLabRuntime()
    _terminal: TerminalBridge = FakeTerminalBridge()


# --- Domain adapter ---------------------------------------------------------


def _require_argv(params: JsonObject) -> tuple[str, ...]:
    argv = params.get("argv")
    if not isinstance(argv, list | tuple) or not all(isinstance(a, str) for a in argv):
        raise AdapterParamsError("params.argv must be an array of strings")
    return tuple(str(a) for a in argv)


class FakeCommandExitCheck:
    """:class:`~harness.core.domain_adapter.Check` passing when ``params.argv``
    exits with ``params.expected_exit_code`` inside the lab."""

    def __init__(self, *, timeout_seconds: float, max_output_bytes: int) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def validate_params(self, params: JsonObject) -> None:
        _require_argv(params)
        code = params.get("expected_exit_code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise AdapterParamsError("params.expected_exit_code must be an integer")

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        argv = _require_argv(params)
        result = lab.exec(
            lab_instance_id,
            ExecRequest(
                argv=argv,
                timeout_seconds=self._timeout_seconds,
                max_output_bytes=self._max_output_bytes,
                env={},
                workdir=None,
            ),
        )
        return CheckObservation(
            passed=not result.timed_out and result.exit_code == params["expected_exit_code"],
            observed={
                "argv": list(argv),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        )


class FakeFixtureProvider:
    """:class:`~harness.core.domain_adapter.FixtureProvider` copying the params
    into the lab as ``/etc/morphloop/fixture.json``. ``required_params`` are the
    keys ``validate_params`` insists on."""

    def __init__(
        self,
        *,
        limits: ResourceLimits,
        network: NetworkMode,
        required_params: Sequence[str] = (),
    ) -> None:
        self._limits = limits
        self._network: NetworkMode = network
        self._required = tuple(required_params)

    def validate_params(self, params: JsonObject) -> None:
        missing = [key for key in self._required if key not in params]
        if missing:
            raise AdapterParamsError(f"missing params: {', '.join(missing)}")

    def build_lab_spec(self, image: ImageRef, params: JsonObject) -> LabSpec:
        content = json.dumps(to_plain_object(params), sort_keys=True).encode()
        return LabSpec(
            image=image,
            limits=self._limits,
            network=self._network,
            env={},
            files=(LabFile(path="/etc/morphloop/fixture.json", content=content, mode=0o644),),
            workdir=None,
        )


class FakeCommandDetector:
    """Treats each input line (ended by CR or LF) as one command; ignores output."""

    def __init__(self) -> None:
        self._line = bytearray()

    def feed_input(self, data: bytes) -> Sequence[DetectedCommand]:
        commands: list[DetectedCommand] = []
        for byte in data:
            if byte in b"\r\n":
                text = self._line.decode("utf-8", errors="replace").strip()
                self._line.clear()
                if text:
                    commands.append(DetectedCommand(command=text, cwd=None))
            else:
                self._line.append(byte)
        return commands

    def feed_output(self, data: bytes) -> Sequence[DetectedCommand]:
        return ()


class FakeTerminalTool:
    """:class:`~harness.core.domain_adapter.TerminalTool` running ``argv``."""

    def __init__(self, argv: tuple[str, ...] = ("/bin/sh", "-l")) -> None:
        self._launch = TerminalLaunch(argv=argv, env={}, workdir=None)

    def launch(self) -> TerminalLaunch:
        return self._launch

    def new_command_detector(self) -> CommandDetector:
        return FakeCommandDetector()


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeDomainAdapter:
    """:class:`~harness.core.domain_adapter.DomainAdapter` built from given items."""

    adapter_id: str
    version: str
    fixtures: Mapping[str, FixtureProvider] = field(default_factory=dict)
    checks: Mapping[str, Check] = field(default_factory=dict)
    tools: Mapping[str, TerminalTool] = field(default_factory=dict)


def _check_domain_adapter_conformance() -> None:  # pragma: no cover - evaluated by mypy only
    limits = ResourceLimits(cpus=1, memory_bytes=1, pids=1, lifetime_seconds=1)
    _check: Check = FakeCommandExitCheck(timeout_seconds=1, max_output_bytes=1)
    _fixture: FixtureProvider = FakeFixtureProvider(limits=limits, network="none")
    _tool: TerminalTool = FakeTerminalTool()
    _detector: CommandDetector = FakeCommandDetector()
    _adapter: DomainAdapter = FakeDomainAdapter(adapter_id="fake", version="0.1.0")
