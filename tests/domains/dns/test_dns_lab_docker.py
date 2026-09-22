"""Integration test: the DNS adapter against a real Docker lab (AC-J2).

The lab container is created directly with the Docker SDK the way the Docker
lab runtime does it (image default command with a TTY, files copied in before
start, no network, all capabilities dropped, no-new-privileges) from the spec
``dns.resolver_lab`` builds. Checks run through a minimal ``LabRuntime.exec``
over ``docker exec``. Skipped when Docker or the pack's image is unavailable;
containers are always removed.
"""

from __future__ import annotations

import io
import select
import socket
import tarfile
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from dns_lab_testdata import FAULT_PARAMS, IMAGE

import domains.dns
from domains.dns.fixtures import READY_PATH
from harness.core.domain_adapter import DetectedCommand
from harness.core.ports import ExecRequest, ExecResult, LabSpec

docker = pytest.importorskip("docker")

ADAPTER = domains.dns.adapter()
IMAGE_REF = f"{IMAGE.repository}@{IMAGE.digest}"

# Mirrors the checks and reference solution of
# contents/software-engineering/activities/gen-dns-wrong-nameserver-001*.json.
CHECKS: list[tuple[str, dict[str, Any]]] = [
    (
        "resolver_answers",
        {
            "name": "api.corp.internal",
            "record_type": "A",
            "expected_value": "127.0.10.20",
            "timeout_seconds": 5,
        },
    ),
    (
        "name_resolves",
        {"name": "api.corp.internal", "expected_address": "127.0.10.20", "timeout_seconds": 5},
    ),
    (
        "http_status",
        {
            "url": "http://api.corp.internal:8080/health",
            "expected_status": 200,
            "timeout_seconds": 5,
        },
    ),
    ("resolver_config", {"nameserver": "127.0.0.53"}),
    (
        "command_exit",
        {
            "argv": ["curl", "-fsS", "--max-time", "5", "http://api.corp.internal:8080/health"],
            "expected_exit_code": 0,
            "timeout_seconds": 10,
        },
    ),
]
SOLUTION = (
    "sh",
    "-c",
    "printf 'nameserver 127.0.0.53\\noptions timeout:1 attempts:1\\n' > /etc/resolv.conf",
)


class _DockerExecLab:
    """``LabRuntime.exec`` over ``docker exec`` (the rest of the Port is unused).

    ``timeout_seconds`` is not enforced here; every command used has its own.
    """

    def __init__(self, container: Any) -> None:
        self._container = container

    def exec(self, lab_instance_id: str, request: ExecRequest) -> ExecResult:
        code, (out, err) = self._container.exec_run(
            list(request.argv),
            environment=dict(request.env),
            workdir=request.workdir,
            demux=True,
        )
        out, err = out or b"", err or b""
        cap = request.max_output_bytes
        return ExecResult(
            exit_code=code,
            stdout=out[:cap],
            stderr=err[:cap],
            timed_out=False,
            stdout_truncated=len(out) > cap,
            stderr_truncated=len(err) > cap,
        )


def _tar(spec: LabSpec) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dirs = sorted({"/".join(f.path.split("/")[:i]) for f in spec.files
                       for i in range(2, f.path.count("/") + 1)})  # fmt: skip
        for d in dirs:
            info = tarfile.TarInfo(d.lstrip("/"))
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        for f in spec.files:
            info = tarfile.TarInfo(f.path.lstrip("/"))
            info.size, info.mode = len(f.content), f.mode
            tar.addfile(info, io.BytesIO(f.content))
    return buf.getvalue()


@pytest.fixture(scope="module")
def docker_client() -> Iterator[Any]:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "no Docker here"
        pytest.skip(f"Docker unavailable: {exc}")
    try:
        client.images.get(IMAGE_REF)
    except docker.errors.ImageNotFound:
        try:
            client.images.pull(IMAGE.repository, tag=IMAGE.digest)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"lab image {IMAGE_REF} unavailable: {exc}")
    yield client
    client.close()


@pytest.fixture
def lab(docker_client: Any) -> Iterator[Any]:
    spec = ADAPTER.fixtures["resolver_lab"].build_lab_spec(IMAGE, FAULT_PARAMS)
    container = docker_client.containers.create(
        IMAGE_REF,
        name=f"morphloop-test-dns-{uuid.uuid4().hex[:12]}",
        tty=True,
        stdin_open=True,
        network_mode=spec.network,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        environment=dict(spec.env),
        nano_cpus=int(spec.limits.cpus * 1e9),
        mem_limit=spec.limits.memory_bytes,
        pids_limit=spec.limits.pids,
        labels={"morphloop.test": "domains-dns"},
    )
    try:
        assert container.put_archive("/", _tar(spec))
        container.start()
        deadline = time.monotonic() + 20
        while container.exec_run(["test", "-e", READY_PATH]).exit_code != 0:
            assert time.monotonic() < deadline, "lab services did not come up"
            time.sleep(0.2)
        yield container
    finally:
        container.remove(force=True)


def _run_checks(runtime: _DockerExecLab) -> dict[str, bool]:
    return {
        name: ADAPTER.checks[name].run(runtime, "lab", params).passed for name, params in CHECKS
    }


def test_checks_fail_when_broken_and_pass_after_reference_solution(lab: Any) -> None:
    runtime = _DockerExecLab(lab)
    assert _run_checks(runtime) == {name: False for name, _ in CHECKS}
    assert lab.exec_run(list(SOLUTION)).exit_code == 0
    assert _run_checks(runtime) == {name: True for name, _ in CHECKS}


def test_pinning_in_etc_hosts_does_not_pass_resolver_answers(lab: Any) -> None:
    runtime = _DockerExecLab(lab)
    pin = ("sh", "-c", "echo '127.0.10.20 api.corp.internal' >> /etc/hosts")
    assert lab.exec_run(list(pin)).exit_code == 0
    passed = _run_checks(runtime)
    assert passed["name_resolves"] and passed["http_status"] and passed["command_exit"]
    assert not passed["resolver_answers"]


def test_terminal_reports_commands(docker_client: Any, lab: Any) -> None:
    launch = ADAPTER.tools["terminal"].launch()
    detector = ADAPTER.tools["terminal"].new_command_detector()
    api = docker_client.api
    exec_id = api.exec_create(
        lab.id, list(launch.argv), stdin=True, tty=True, environment=dict(launch.env)
    )["Id"]
    attached = api.exec_start(exec_id, socket=True, tty=True)
    sock: socket.socket = getattr(attached, "_sock", attached)
    detected: list[DetectedCommand] = []

    def pump(until: int, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while len(detected) < until and time.monotonic() < deadline:
            ready, _, _ = select.select([sock], [], [], 0.2)
            if ready:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                detected.extend(detector.feed_output(chunk))

    try:
        for line in (b"cd /tmp\n", b"\n", b" echo  spaced\n", b"dig +short api.corp.internal\n"):
            sock.sendall(line)
            pump(until=len(detected) + (0 if line == b"\n" else 1), timeout=10.0)
        sock.sendall(b"exit\n")
        pump(until=4, timeout=5.0)
    finally:
        sock.close()
    assert detected == [
        DetectedCommand(command="cd /tmp", cwd="/root"),
        DetectedCommand(command="echo  spaced", cwd="/tmp"),
        DetectedCommand(command="dig +short api.corp.internal", cwd="/tmp"),
        DetectedCommand(command="exit", cwd="/tmp"),
    ]
