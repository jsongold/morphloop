"""Lab runtime Port (ADR-0009, ADR-0015; ``docs/ARCHITECTURE.md`` Sandbox model).

Starts, resets and destroys disposable, isolated lab instances and runs
commands inside them. The Docker adapter lives in ``harness/adapters/docker_lab/``.
The domain-specific part of a lab (turning an EnvironmentDefinition's fixture
params into a :class:`LabSpec`) belongs to a domain adapter's fixture provider,
not to this Port.

Security by construction. The spec types deliberately have no field for host
bind mounts or volumes, the Docker socket, privileged mode, added
capabilities, devices, host networking or host PID/IPC namespaces, so core
cannot ask for them. Files reach a lab only as bytes copied in
(:class:`LabFile`). Commands are argv tuples, never shell strings (:data:`Argv`),
and run only inside the lab. Adapters MUST additionally: run the lab
unprivileged with all capabilities dropped that the image does not strictly
need and ``no-new-privileges``; pull the image by digest only; apply every
:class:`ResourceLimits` value; and never mount anything from the host.

Lab instance ids are assigned by core (ADR-0016: IDs are arguments, not a
Port). Resetting creates a new LabInstance with a new id (``lab.reset`` /
``lab.started`` with ``trigger: reset``).

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

type Argv = tuple[str, ...]
type NetworkMode = Literal["none", "isolated"]
type LabStatus = Literal["starting", "running", "exited", "absent"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# Same pattern as pack/defs.json#/$defs/oci_image.repository: no tag, no digest.
_REPOSITORY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(:[0-9]{1,5})?(/[a-z0-9][a-z0-9._-]*)*$")


def check_argv(argv: Argv) -> None:
    """Raise ``ValueError`` unless ``argv`` is a safe sandbox argv.

    Mirrors ``contracts/schemas/pack/defs.json#/$defs/sandbox_argv``: a non-empty
    ``tuple`` (a ``str`` is rejected, although it is a ``Sequence[str]``) whose
    first element has no whitespace, so a whole command line cannot be passed
    as one element, and no element contains NUL.
    """
    if not isinstance(argv, tuple):
        raise ValueError(f"argv must be a tuple of str, got {type(argv).__name__}")
    if not argv:
        raise ValueError("argv must not be empty")
    for arg in argv:
        if not isinstance(arg, str):
            raise ValueError(f"argv elements must be str, got {type(arg).__name__}")
        if "\x00" in arg:
            raise ValueError("argv elements must not contain NUL")
    if not argv[0] or any(ch.isspace() for ch in argv[0]):
        raise ValueError(f"argv[0] must be non-empty without whitespace, got {argv[0]!r}")


def _check_container_path(name: str, path: str) -> None:
    if not path.startswith("/") or "\x00" in path or "/../" in f"{path}/":
        raise ValueError(f"{name} must be an absolute path inside the lab, got {path!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageRef:
    """Lab image pinned by digest, never by tag (``pack/defs.json#/$defs/oci_image``)."""

    repository: str
    digest: str

    def __post_init__(self) -> None:
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError(f"digest must be 'sha256:<64 hex>', got {self.digest!r}")
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise ValueError(f"repository must not carry a tag or digest: {self.repository!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceLimits:
    """Hard limits applied to a lab instance. Every value is required.

    ``lifetime_seconds`` is the wall-clock time after which the adapter
    destroys the lab even if nobody called :meth:`LabRuntime.destroy`.
    """

    cpus: float
    memory_bytes: int
    pids: int
    lifetime_seconds: int

    def __post_init__(self) -> None:
        if self.cpus <= 0 or self.memory_bytes <= 0 or self.pids <= 0:
            raise ValueError("cpus, memory_bytes and pids must be positive")
        if self.lifetime_seconds <= 0:
            raise ValueError("lifetime_seconds must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class LabFile:
    """A file copied into the lab before its main process starts.

    Copied, never bind-mounted: the lab cannot see or change the host file.
    """

    path: str
    content: bytes
    mode: int

    def __post_init__(self) -> None:
        _check_container_path("LabFile.path", self.path)
        if not 0 <= self.mode <= 0o7777:
            raise ValueError(f"mode must be a permission mode, got {oct(self.mode)}")


@dataclass(frozen=True, slots=True, kw_only=True)
class LabSpec:
    """Everything needed to start one lab instance.

    ``network``: ``"none"`` gives loopback only; ``"isolated"`` gives a private
    per-lab network with no route to the host or to other labs, and no internet
    egress. ``workdir`` ``None`` means the image default.
    """

    image: ImageRef
    limits: ResourceLimits
    network: NetworkMode
    env: Mapping[str, str]
    files: Sequence[LabFile]
    workdir: str | None

    def __post_init__(self) -> None:
        if self.workdir is not None:
            _check_container_path("workdir", self.workdir)


@dataclass(frozen=True, slots=True, kw_only=True)
class LabInfo:
    """A started lab instance.

    ``runtime_ref`` is an opaque adapter-specific handle (e.g. a container id).
    Core never interprets it; it only hands it to the terminal bridge adapter
    that wiring pairs with this runtime. ``image_digest`` is the digest actually
    running, recorded as lab provenance.
    """

    lab_instance_id: str
    runtime_ref: str
    image_digest: str
    status: LabStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecRequest:
    """A command to run inside a lab (e.g. a deterministic check).

    ``timeout_seconds`` and ``max_output_bytes`` are required: the caller takes
    them from the pack or the domain adapter; the Port has no defaults.
    """

    argv: Argv
    timeout_seconds: float
    max_output_bytes: int
    env: Mapping[str, str]
    workdir: str | None

    def __post_init__(self) -> None:
        check_argv(self.argv)
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0:
            raise ValueError("timeout_seconds and max_output_bytes must be positive")
        if self.workdir is not None:
            _check_container_path("workdir", self.workdir)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecResult:
    """Observed result of :meth:`LabRuntime.exec`.

    ``exit_code`` is ``None`` when the command was killed on timeout
    (``timed_out``). Each stream keeps at most ``max_output_bytes`` bytes;
    the ``*_truncated`` flags say whether more was produced.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class LabRuntimeError(Exception):
    """Base class for lab runtime failures raised by adapters."""


class LabNotFoundError(LabRuntimeError):
    """The lab instance does not exist or is no longer running."""


class LabRuntime(Protocol):
    """Disposable, isolated lab instances (synchronous; see ``harness.core.ports``)."""

    def start(self, lab_instance_id: str, spec: LabSpec) -> LabInfo:
        """Start a new lab from ``spec`` and return once it is running.

        Raises :class:`LabRuntimeError` if the id is already in use or the lab
        cannot be started; a partially started lab is cleaned up first.
        """
        ...

    def reset(self, lab_instance_id: str, *, new_lab_instance_id: str, spec: LabSpec) -> LabInfo:
        """Destroy ``lab_instance_id`` and start a fresh lab from ``spec``.

        Nothing from the old instance survives. Returns the new instance.
        """
        ...

    def destroy(self, lab_instance_id: str) -> None:
        """Destroy the lab and everything it created. Idempotent: an unknown or
        already destroyed id is a no-op."""
        ...

    def exec(self, lab_instance_id: str, request: ExecRequest) -> ExecResult:
        """Run ``request.argv`` inside the lab, without a shell, and wait for it.

        A non-zero exit or a timeout is a result, not an error. Raises
        :class:`LabNotFoundError` if the lab is not running.
        """
        ...

    def status(self, lab_instance_id: str) -> LabStatus:
        """Return the lab's lifecycle state; ``"absent"`` for unknown or destroyed ids."""
        ...
