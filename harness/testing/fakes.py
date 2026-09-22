"""In-memory fakes of the five Ports (``harness.core.ports``) for tests.

Each fake implements its Port structurally and keeps just enough behaviour for
core and API tests to run without Postgres, an LLM provider, Docker or a PTY:

- :class:`InMemoryEventStore` -- positions, idempotent append with conflict
  detection, commit/rollback of events and projections together, reads.
- :class:`FakeLLMProvider` -- returns scripted outputs (or raises scripted
  errors) and records every request.
- :class:`FakeLabRuntime` -- tracks lab lifecycle; command results come from a
  caller-supplied handler.
- :class:`FakeTerminalBridge` -- sessions that echo input to output.
- :class:`InMemoryPackSource` -- packs as ``{location: {path: bytes}}``.

This module may import ``harness.core``; core must never import it.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from harness.core.ports import (
    AppendRequest,
    AppendResult,
    EventStore,
    EventStoreError,
    EventTransaction,
    ExecRequest,
    ExecResult,
    IdempotencyConflictError,
    JsonObject,
    LabInfo,
    LabNotFoundError,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    LabStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    PackFileNotFoundError,
    PackLocationNotFoundError,
    PackSource,
    StoredEvent,
    TerminalBridge,
    TerminalBridgeError,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
    check_pack_path,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- Event store ------------------------------------------------------------


@dataclass
class _EventLog:
    events: list[StoredEvent] = field(default_factory=list)
    by_key: dict[str, StoredEvent] = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)
    projections: dict[str, dict[str, JsonObject]] = field(default_factory=dict)
    next_position: int = 1

    def copy(self) -> _EventLog:
        return _EventLog(
            events=list(self.events),
            by_key=dict(self.by_key),
            event_ids=set(self.event_ids),
            projections={name: dict(docs) for name, docs in self.projections.items()},
            next_position=self.next_position,
        )


class _InMemoryTransaction:
    """Works on a private copy of the log; the store swaps it in on commit."""

    def __init__(self, log: _EventLog, now: Callable[[], datetime]) -> None:
        self.log = log
        self.locked_learners: set[str] = set()
        self._now = now
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise EventStoreError("transaction already ended")

    def append(self, request: AppendRequest) -> AppendResult:
        self._check_open()
        key = request.idempotency_key
        if key is not None and key in self.log.by_key:
            existing = self.log.by_key[key]
            if not request.same_content_as(existing):
                raise IdempotencyConflictError(key, existing)
            return AppendResult(event=existing, created=False)
        if request.event_id in self.log.event_ids:
            raise EventStoreError(f"duplicate event_id {request.event_id!r}")
        stored = StoredEvent(
            event_id=request.event_id,
            event_type=request.event_type,
            event_version=request.event_version,
            occurred_at=request.occurred_at,
            idempotency_key=request.idempotency_key,
            causation_id=request.causation_id,
            correlation_id=request.correlation_id,
            learner_id=request.learner_id,
            session_id=request.session_id,
            attempt_id=request.attempt_id,
            activity_definition_id=request.activity_definition_id,
            actor=request.actor,
            payload=request.payload,
            position=self.log.next_position,
            recorded_at=self._now(),
        )
        self.log.next_position += 1
        self.log.events.append(stored)
        self.log.event_ids.add(stored.event_id)
        if key is not None:
            self.log.by_key[key] = stored
        return AppendResult(event=stored, created=True)

    def lock_learner(self, learner_id: str) -> None:
        # The store serializes whole transactions, so the lock is only recorded.
        self._check_open()
        self.locked_learners.add(learner_id)

    def get_projection(self, name: str, key: str) -> JsonObject | None:
        self._check_open()
        return self.log.projections.get(name, {}).get(key)

    def list_projection(
        self, name: str, *, key_prefix: str = ""
    ) -> Sequence[tuple[str, JsonObject]]:
        self._check_open()
        docs = self.log.projections.get(name, {})
        return [(k, docs[k]) for k in sorted(docs) if k.startswith(key_prefix)]

    def put_projection(self, name: str, key: str, document: JsonObject) -> None:
        self._check_open()
        self.log.projections.setdefault(name, {})[key] = document

    def delete_projection(self, name: str, key: str) -> None:
        self._check_open()
        self.log.projections.get(name, {}).pop(key, None)

    def clear_projection(self, name: str) -> None:
        self._check_open()
        self.log.projections.pop(name, None)


class InMemoryEventStore:
    """:class:`~harness.core.ports.EventStore` kept in process memory.

    Transactions are fully serialized by one re-entrant lock, which trivially
    satisfies per-learner serialization. ``now`` supplies ``recorded_at``.
    """

    def __init__(self, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._log = _EventLog()
        self._now = now
        self._lock = threading.RLock()

    def transaction(self) -> AbstractContextManager[EventTransaction]:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[_InMemoryTransaction]:
        with self._lock:
            tx = _InMemoryTransaction(self._log.copy(), self._now)
            try:
                yield tx
            finally:
                tx.close()
            # Reached only when the block did not raise: commit.
            self._log = tx.log

    def read_session(
        self,
        session_id: str,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        return self._read(session_id, after_position, until_position, limit)

    def read_all(
        self,
        *,
        after_position: int = 0,
        until_position: int | None = None,
        limit: int | None = None,
    ) -> Sequence[StoredEvent]:
        return self._read(None, after_position, until_position, limit)

    def _read(
        self,
        session_id: str | None,
        after_position: int,
        until_position: int | None,
        limit: int | None,
    ) -> list[StoredEvent]:
        with self._lock:
            events = self._log.events
        selected = [
            e
            for e in events
            if e.position > after_position
            and (until_position is None or e.position <= until_position)
            and (session_id is None or e.session_id == session_id)
        ]
        return selected if limit is None else selected[:limit]


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
    """

    def __init__(self, exec_handler: Callable[[str, ExecRequest], ExecResult] = _succeed) -> None:
        self.labs: dict[str, LabSpec] = {}
        self.exec_calls: list[tuple[str, ExecRequest]] = []
        self._exec_handler = exec_handler

    def start(self, lab_instance_id: str, spec: LabSpec) -> LabInfo:
        if lab_instance_id in self.labs:
            raise LabRuntimeError(f"lab {lab_instance_id!r} already exists")
        self.labs[lab_instance_id] = spec
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


# --- Pack source ------------------------------------------------------------


class InMemoryPackSource:
    """:class:`~harness.core.ports.PackSource` over ``{location: {path: bytes}}``."""

    def __init__(self, packs: Mapping[str, Mapping[str, bytes]]) -> None:
        for files in packs.values():
            for path in files:
                check_pack_path(path)
        self._packs = {loc: dict(files) for loc, files in packs.items()}

    def _files(self, location: str) -> dict[str, bytes]:
        try:
            return self._packs[location]
        except KeyError:
            raise PackLocationNotFoundError(location) from None

    def list_files(self, location: str) -> Sequence[str]:
        return sorted(self._files(location))

    def read_bytes(self, location: str, path: str) -> bytes:
        check_pack_path(path)
        files = self._files(location)
        try:
            return files[path]
        except KeyError:
            raise PackFileNotFoundError(f"{location}:{path}") from None


def _check_conformance() -> None:  # pragma: no cover - evaluated by mypy only
    _store: EventStore = InMemoryEventStore()
    _llm: LLMProvider = FakeLLMProvider()
    _lab: LabRuntime = FakeLabRuntime()
    _terminal: TerminalBridge = FakeTerminalBridge()
    _pack: PackSource = InMemoryPackSource({})
