"""The five v0.1 Ports: interfaces core owns and technical adapters implement.

ADR-0015, ADR-0016, ADR-0017. One module per Port:

- ``event_store`` -- append-only event log, idempotent append, unit of work
  with projection access, per-learner serialization, rebuild reads.
- ``llm`` -- provider-neutral structured-output LLM call.
- ``lab_runtime`` -- disposable, isolated lab instances and in-lab commands.
- ``terminal_bridge`` -- interactive PTY sessions inside a lab (asyncio).
- ``pack_source`` -- list and read the raw files of a pack location.

Clock and ID generation are not Ports (ADR-0016): core functions take times
and ids as arguments.

Conventions shared by every Port:

- Ports are ``typing.Protocol`` classes (structural typing), so adapters and
  test fakes implement them without importing a base class. They are not
  ``runtime_checkable``: ``isinstance`` would check method names only and
  give false confidence; conformance is checked by mypy.
- Value types are hand-written frozen dataclasses (``slots``, ``kw_only``),
  not TypedDicts. Reasons: construction is checked by mypy with no implicit
  optional keys; values are immutable once built, which matters for requests
  shared between a caller and an adapter; ``__post_init__`` enforces
  invariants no type can (argv safety, digest-pinned images, aware
  timestamps); and equality is structural. Where a value crosses a language
  boundary it has a ``to_dict()`` whose result is checked against
  ``contracts/`` by contract tests (ADR-0017: no generated types). JSON
  payloads inside them are typed as :data:`JsonObject`.
- Sync vs async: every Port is synchronous except the terminal bridge. v0.1
  runs HTTP handlers in FastAPI's thread pool and uses the synchronous
  SQLAlchemy engine; only the terminal is a long-lived duplex stream on the
  WebSocket event loop.
- This package imports only the standard library.
"""

from harness.core.ports.event_store import (
    Actor,
    AppendRequest,
    AppendResult,
    EventEnvelope,
    EventStore,
    EventStoreError,
    EventTransaction,
    IdempotencyConflictError,
    StoredEvent,
)
from harness.core.ports.json_types import (
    JsonObject,
    JsonScalar,
    JsonValue,
    PlainJson,
    format_timestamp,
    to_plain_json,
    to_plain_object,
)
from harness.core.ports.lab_runtime import (
    Argv,
    ExecRequest,
    ExecResult,
    ImageRef,
    LabFile,
    LabInfo,
    LabNotFoundError,
    LabNotReadyError,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    LabStatus,
    NetworkMode,
    ReadinessProbe,
    ResourceLimits,
    check_argv,
)
from harness.core.ports.llm import (
    GenerationParameter,
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMRole,
    MessageRole,
)
from harness.core.ports.pack_source import (
    PackFileNotFoundError,
    PackLocationNotFoundError,
    PackPathError,
    PackSource,
    PackSourceError,
    check_pack_path,
)
from harness.core.ports.terminal_bridge import (
    TerminalBridge,
    TerminalBridgeError,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
)

__all__ = [
    "Actor",
    "AppendRequest",
    "AppendResult",
    "Argv",
    "EventEnvelope",
    "EventStore",
    "EventStoreError",
    "EventTransaction",
    "ExecRequest",
    "ExecResult",
    "GenerationParameter",
    "IdempotencyConflictError",
    "ImageRef",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "LLMError",
    "LLMMessage",
    "LLMOutputError",
    "LLMProvenance",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMRole",
    "LabFile",
    "LabInfo",
    "LabNotFoundError",
    "LabNotReadyError",
    "LabRuntime",
    "LabRuntimeError",
    "LabSpec",
    "LabStatus",
    "MessageRole",
    "NetworkMode",
    "PackFileNotFoundError",
    "PackLocationNotFoundError",
    "PackPathError",
    "PackSource",
    "PackSourceError",
    "PlainJson",
    "ReadinessProbe",
    "ResourceLimits",
    "StoredEvent",
    "TerminalBridge",
    "TerminalBridgeError",
    "TerminalClosedError",
    "TerminalOpenRequest",
    "TerminalSession",
    "TerminalSize",
    "check_argv",
    "check_pack_path",
    "format_timestamp",
    "to_plain_json",
    "to_plain_object",
]
