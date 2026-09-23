"""Docker implementation of the :class:`~harness.core.ports.LabRuntime` Port.

One lab instance is one Docker container, plus one Docker network when the
spec asks for ``network="isolated"``. Everything goes through the Docker
Engine API (``docker`` SDK); nothing is ever run through a host shell, and
every command is an argv list executed inside the lab container.

``LabInfo.runtime_ref``
    The full Docker container id of the lab. The PTY terminal bridge adapter
    that wiring pairs with this runtime uses it to ``docker exec`` into the
    lab. Nothing else should interpret it.

``LabInfo.image_digest``
    The manifest digest the container was created from. The container is
    created from ``<repository>@<digest>`` (never a tag), and the adapter
    checks that the local image's ``RepoDigests`` contain that digest before
    creating it; the image is pulled by digest if it is not present.

Security decisions (``docs/ARCHITECTURE.md`` Sandbox model / Security
boundaries, ADR-0009):

- Capabilities: ``cap_drop=["ALL"]`` and none added. The Port has no field to
  add capabilities, so a lab gets none. Root in the lab can still read and
  write root-owned files and bind ports below 1024 (Docker sets
  ``net.ipv4.ip_unprivileged_port_start=0`` in the container namespace), which
  is what the v0.1 labs need. A lab that needs a capability requires a Port
  change first.
- ``no-new-privileges``, not privileged, private IPC namespace, own PID
  namespace, no devices, no restart policy.
- Filesystem: no bind mounts, no volumes, no Docker socket. :class:`LabFile`
  values are copied in with ``put_archive`` before the container starts.
  The root filesystem is *writable*: a read-only rootfs is not feasible
  because the Engine refuses ``put_archive`` into a read-only rootfs, and
  practical activities require the learner to edit files anywhere in the lab
  (e.g. ``/etc``). The container is disposable, so writes never outlive it.
- Network: ``"none"`` uses Docker's ``none`` network mode (loopback only).
  ``"isolated"`` creates a per-lab ``internal`` bridge network (no route out,
  no internet egress) with ``com.docker.network.bridge.inhibit_ipv4`` so the
  bridge has no host-side IP address: the host is not reachable at L3, and no
  other lab is attached to the network.
- Resources: every :class:`ResourceLimits` value is applied (``nano_cpus``,
  ``mem_limit`` with ``memswap_limit`` equal to it, i.e. no swap,
  ``pids_limit``). ``lifetime_seconds`` is enforced by a timer in this process
  and recorded as a label, so :meth:`DockerLabRuntime.reap_expired` can remove
  labs whose timer died with a previous process; wiring should call it at
  startup.
- Logging driver ``none``: the main process's output is not needed (learners
  work through the terminal bridge), and it must not fill the host disk.

The main process is ``spec.command`` as an argv (never a shell string), or
the image's default command when the spec gives none. It is started with a
TTY and open stdin so that images whose command is a shell keep running.
When the spec declares a :class:`ReadinessProbe`, :meth:`DockerLabRuntime.start`
runs it inside the lab every ``interval_seconds`` until it exits ``0`` and
returns only then; if ``timeout_seconds`` pass, or the main process stops
first, the start fails (:class:`LabNotReadyError`) and the lab is cleaned up
like any other failed start. Without a probe, start returns as soon as the
container is running, which only says the main process was created.

Commands that time out are killed with ``SIGKILL``: every exec carries a
random token in its environment, and on timeout a fixed script (``/bin/sh``
from the lab image, token passed as an argument, never interpolated) kills
every process in the lab whose environment contains that token, which
includes the command's descendants. Images without ``/bin/sh`` cannot be
cleaned up this way; the result still reports ``timed_out``.
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
import select
import struct
import tarfile
import threading
import time
from typing import Any, Protocol, cast

import docker
import docker.errors
from docker.models.containers import Container
from docker.types import LogConfig
from docker.utils.socket import read as _socket_read

from harness.core.ports import (
    ExecRequest,
    ExecResult,
    LabFile,
    LabInfo,
    LabNotFoundError,
    LabNotReadyError,
    LabRuntimeError,
    LabSpec,
    LabStatus,
    ReadinessProbe,
)

LABEL_MANAGED = "io.morphloop.lab.managed"
LABEL_LAB_ID = "io.morphloop.lab.instance_id"
LABEL_EXPIRES_AT = "io.morphloop.lab.expires_at"
_MANAGED_VALUE = "docker_lab"

_EXEC_TOKEN_ENV = "MORPHLOOP_EXEC_TOKEN"
# Adapter-authored, constant. The token arrives as $1; nothing is interpolated.
_KILL_BY_TOKEN_SCRIPT = (
    't="MORPHLOOP_EXEC_TOKEN=$1"; '
    "for d in /proc/[0-9]*; do "
    "if tr '\\000' '\\n' < \"$d/environ\" 2>/dev/null | grep -qxF \"$t\"; then "
    'kill -KILL "${d#/proc/}" 2>/dev/null; '
    "fi; done; true"
)

_READINESS_MAX_OUTPUT_BYTES = 4096
"""Output kept from one readiness attempt; only its tail reaches the error."""
_READINESS_ERROR_CHARS = 200

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_STATUS_MAP: dict[str, LabStatus] = {
    "created": "starting",
    "restarting": "starting",
    "running": "running",
    "paused": "exited",
    "exited": "exited",
    "dead": "exited",
    "removing": "exited",
}


class _ReadableSocket(Protocol):
    def fileno(self) -> int: ...
    def close(self) -> None: ...


def _name_suffix(lab_instance_id: str) -> str:
    if _SAFE_NAME_RE.fullmatch(lab_instance_id):
        return lab_instance_id
    return hashlib.sha256(lab_instance_id.encode()).hexdigest()[:32]


def _tail(output: bytes) -> str:
    """The end of a readiness attempt's output, as one printable line."""
    text = output.decode("utf-8", errors="replace").strip()
    return (text[-_READINESS_ERROR_CHARS:] or "no output").replace("\n", " ")


def _files_tar(files: list[LabFile]) -> bytes:
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in files:
            info = tarfile.TarInfo(name=f.path.lstrip("/"))
            info.size = len(f.content)
            info.mode = f.mode
            info.uid = info.gid = 0
            info.mtime = now
            tar.addfile(info, io.BytesIO(f.content))
    return buf.getvalue()


class _Capture:
    """Keeps at most ``limit`` bytes and remembers whether more arrived."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._buf = bytearray()
        self.truncated = False

    def add(self, data: bytes) -> None:
        room = self._limit - len(self._buf)
        if len(data) > room:
            self.truncated = True
        if room > 0:
            self._buf += data[:room]

    def value(self) -> bytes:
        return bytes(self._buf)


class _Deadline(Exception):
    pass


def _read_exactly(sock: _ReadableSocket, n: int, deadline: float) -> bytes:
    """Read ``n`` bytes; ``b""`` on EOF before the first byte; raise on deadline."""
    data = b""
    while len(data) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Deadline
        ready, _, _ = select.select([sock.fileno()], [], [], remaining)
        if not ready:
            raise _Deadline
        chunk = _socket_read(sock, n - len(data))
        if chunk is None:
            continue
        if not chunk:
            if data:
                raise LabRuntimeError("exec stream ended mid-frame")
            return b""
        data += chunk
    return data


class DockerLabRuntime:
    """:class:`~harness.core.ports.LabRuntime` backed by the Docker Engine."""

    def __init__(self, client: docker.DockerClient) -> None:
        self._client = client
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------

    def start(self, lab_instance_id: str, spec: LabSpec) -> LabInfo:
        if self._find_container(lab_instance_id) is not None:
            raise LabRuntimeError(f"lab {lab_instance_id!r} already exists")
        try:
            return self._start(lab_instance_id, spec)
        except BaseException as exc:
            try:
                self._remove_resources(lab_instance_id)
            except docker.errors.DockerException:
                pass  # Report the original failure; reap_expired() retries.
            if isinstance(exc, LabRuntimeError):
                raise
            if isinstance(exc, docker.errors.DockerException):
                raise LabRuntimeError(f"cannot start lab {lab_instance_id!r}: {exc}") from exc
            raise

    def reset(self, lab_instance_id: str, *, new_lab_instance_id: str, spec: LabSpec) -> LabInfo:
        self.destroy(lab_instance_id)
        return self.start(new_lab_instance_id, spec)

    def destroy(self, lab_instance_id: str) -> None:
        with self._lock:
            timer = self._timers.pop(lab_instance_id, None)
        if timer is not None:
            timer.cancel()
        try:
            self._remove_resources(lab_instance_id)
        except docker.errors.DockerException as exc:
            raise LabRuntimeError(f"cannot destroy lab {lab_instance_id!r}: {exc}") from exc

    def status(self, lab_instance_id: str) -> LabStatus:
        container = self._find_container(lab_instance_id)
        if container is None:
            return "absent"
        return _STATUS_MAP.get(str(container.status), "exited")

    def reap_expired(self) -> list[str]:
        """Destroy every managed lab whose lifetime has passed; return their ids."""
        now = time.time()
        reaped: list[str] = []
        for container in self._managed_containers({}):
            labels = container.labels
            lab_id = labels.get(LABEL_LAB_ID)
            try:
                expires_at = float(labels.get(LABEL_EXPIRES_AT, "inf"))
            except ValueError:
                continue
            if lab_id is not None and expires_at <= now:
                self.destroy(lab_id)
                reaped.append(lab_id)
        return reaped

    # --- exec --------------------------------------------------------------

    def exec(self, lab_instance_id: str, request: ExecRequest) -> ExecResult:
        container = self._find_container(lab_instance_id)
        if container is None or container.status != "running":
            raise LabNotFoundError(lab_instance_id)
        token = secrets.token_hex(16)
        environment = [f"{k}={v}" for k, v in request.env.items()]
        environment.append(f"{_EXEC_TOKEN_ENV}={token}")
        api = self._client.api
        try:
            exec_id = str(
                api.exec_create(
                    container.id,
                    list(request.argv),
                    stdout=True,
                    stderr=True,
                    stdin=False,
                    tty=False,
                    privileged=False,
                    environment=environment,
                    workdir=request.workdir,
                )["Id"]
            )
            deadline = time.monotonic() + request.timeout_seconds
            sock = cast(_ReadableSocket, api.exec_start(exec_id, socket=True))
            stdout = _Capture(request.max_output_bytes)
            stderr = _Capture(request.max_output_bytes)
            timed_out = False
            try:
                while True:
                    header = _read_exactly(sock, 8, deadline)
                    if not header:
                        break
                    stream_id, size = struct.unpack(">BxxxL", header)
                    payload = _read_exactly(sock, size, deadline) if size else b""
                    (stderr if stream_id == 2 else stdout).add(payload)
            except _Deadline:
                timed_out = True
            finally:
                # docker-py pins the HTTP response on the socket; closing the
                # response closes the socket and releases the connection.
                response = getattr(sock, "_response", None)
                (response or sock).close()
            # The stream can end before the process does (it closed its
            # output); keep waiting until the same deadline.
            exit_code = None if timed_out else self._wait_exit_code(exec_id, deadline)
            if exit_code is None:
                timed_out = True
                self._kill_by_token(container, token)
        except docker.errors.NotFound as exc:
            raise LabNotFoundError(lab_instance_id) from exc
        except docker.errors.DockerException as exc:
            raise LabRuntimeError(f"exec failed in lab {lab_instance_id!r}: {exc}") from exc
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout.value(),
            stderr=stderr.value(),
            timed_out=timed_out,
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
        )

    # --- internals ---------------------------------------------------------

    def _start(self, lab_instance_id: str, spec: LabSpec) -> LabInfo:
        image_ref = self._ensure_image(spec.image.repository, spec.image.digest)
        suffix = _name_suffix(lab_instance_id)
        expires_at = time.time() + spec.limits.lifetime_seconds
        labels = {
            LABEL_MANAGED: _MANAGED_VALUE,
            LABEL_LAB_ID: lab_instance_id,
            LABEL_EXPIRES_AT: f"{expires_at:.3f}",
        }
        if spec.network == "none":
            network_mode = "none"
        else:
            network = self._client.networks.create(
                f"morphloop-lab-net-{suffix}",
                driver="bridge",
                internal=True,
                enable_ipv6=False,
                labels=labels,
                options={"com.docker.network.bridge.inhibit_ipv4": "true"},
            )
            network_mode = str(network.name)
        limits = spec.limits
        container = self._client.containers.create(
            image_ref,
            command=None if spec.command is None else list(spec.command),
            name=f"morphloop-lab-{suffix}",
            labels=labels,
            environment=dict(spec.env),
            working_dir=spec.workdir,
            tty=True,
            stdin_open=True,
            network_mode=network_mode,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            privileged=False,
            ipc_mode="private",
            read_only=False,
            nano_cpus=int(limits.cpus * 1_000_000_000),
            mem_limit=limits.memory_bytes,
            memswap_limit=limits.memory_bytes,
            pids_limit=limits.pids,
            log_config=LogConfig(type="none"),
        )
        if spec.files:
            if not container.put_archive("/", _files_tar(list(spec.files))):
                raise LabRuntimeError(f"cannot copy files into lab {lab_instance_id!r}")
        container.start()
        container.reload()
        if container.status != "running":
            raise LabRuntimeError(
                f"lab {lab_instance_id!r} did not stay running (status {container.status!r})"
            )
        if spec.readiness is not None:
            self._await_ready(lab_instance_id, container, spec.readiness)
        timer = threading.Timer(limits.lifetime_seconds, self._expire, args=(lab_instance_id,))
        timer.daemon = True
        with self._lock:
            self._timers[lab_instance_id] = timer
        timer.start()
        return LabInfo(
            lab_instance_id=lab_instance_id,
            runtime_ref=str(container.id),
            image_digest=spec.image.digest,
            status="running",
        )

    def _await_ready(
        self, lab_instance_id: str, container: Container, probe: ReadinessProbe
    ) -> None:
        """Poll ``probe`` inside the lab until it exits 0, or give up.

        Each attempt may use the whole remaining budget, so a probe that hangs
        costs no more than ``timeout_seconds`` in total. The container is
        re-checked before every attempt: a main process that exits (a failed
        boot script) is reported as such instead of as a timeout.
        """
        deadline = time.monotonic() + probe.timeout_seconds
        attempts = 0
        last = "it was never run"
        while True:
            container.reload()
            if container.status != "running":
                raise LabNotReadyError(
                    f"lab {lab_instance_id!r} stopped before it was ready "
                    f"(status {container.status!r}); readiness probe {list(probe.argv)}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempts += 1
            result = self.exec(
                lab_instance_id,
                ExecRequest(
                    argv=probe.argv,
                    timeout_seconds=remaining,
                    max_output_bytes=_READINESS_MAX_OUTPUT_BYTES,
                    env={},
                    workdir=None,
                ),
            )
            if not result.timed_out and result.exit_code == 0:
                return
            last = (
                "it timed out"
                if result.timed_out
                else f"it exited {result.exit_code}: {_tail(result.stderr or result.stdout)}"
            )
            time.sleep(min(probe.interval_seconds, max(0.0, deadline - time.monotonic())))
        raise LabNotReadyError(
            f"lab {lab_instance_id!r} was not ready after {probe.timeout_seconds}s: readiness "
            f"probe {list(probe.argv)} ran {attempts} time(s) and {last}"
        )

    def _ensure_image(self, repository: str, digest: str) -> str:
        ref = f"{repository}@{digest}"
        try:
            image = self._client.images.get(ref)
        except docker.errors.ImageNotFound:
            image = self._client.images.pull(repository, tag=digest)
        repo_digests = image.attrs.get("RepoDigests") or []
        if not any(str(d).split("@", 1)[-1] == digest for d in repo_digests):
            raise LabRuntimeError(f"image {ref!r} does not carry digest {digest}")
        return ref

    def _expire(self, lab_instance_id: str) -> None:
        with self._lock:
            self._timers.pop(lab_instance_id, None)
        try:
            self._remove_resources(lab_instance_id)
        except docker.errors.DockerException:
            pass  # reap_expired() retries via the expires_at label.

    def _managed_containers(self, extra: dict[str, str]) -> list[Container]:
        label_filters = [f"{LABEL_MANAGED}={_MANAGED_VALUE}"]
        label_filters += [f"{k}={v}" for k, v in extra.items()]
        return list(self._client.containers.list(all=True, filters={"label": label_filters}))

    def _find_container(self, lab_instance_id: str) -> Container | None:
        try:
            found = self._managed_containers({LABEL_LAB_ID: lab_instance_id})
        except docker.errors.DockerException as exc:
            raise LabRuntimeError(f"cannot look up lab {lab_instance_id!r}: {exc}") from exc
        return found[0] if found else None

    def _remove_resources(self, lab_instance_id: str) -> None:
        for container in self._managed_containers({LABEL_LAB_ID: lab_instance_id}):
            try:
                container.remove(force=True, v=True)
            except docker.errors.NotFound:
                pass
        label_filters = [f"{LABEL_MANAGED}={_MANAGED_VALUE}", f"{LABEL_LAB_ID}={lab_instance_id}"]
        for network in self._client.networks.list(filters={"label": label_filters}):
            try:
                network.remove()
            except docker.errors.NotFound:
                pass

    def _kill_by_token(self, container: Container, token: str) -> None:
        try:
            container.exec_run(
                ["/bin/sh", "-c", _KILL_BY_TOKEN_SCRIPT, "sh", token],
                stdout=False,
                stderr=False,
            )
        except docker.errors.APIError:
            pass  # No /bin/sh in the image; documented limitation.

    def _wait_exit_code(self, exec_id: str, deadline: float) -> int | None:
        """Return the exec's exit code, or ``None`` if it is still running at ``deadline``."""
        while True:
            info: dict[str, Any] = self._client.api.exec_inspect(exec_id)
            code = info.get("ExitCode")
            if not info.get("Running") and code is not None:
                return int(code)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)
