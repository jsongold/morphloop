"""Integration test: the DNS adapter against a real Docker lab (AC-J2).

The lab is started by the real :class:`DockerLabRuntime` from the spec
``dns.resolver_lab`` builds, so the lab's declared main process (the boot
script) and its readiness probe are exercised exactly as in production, and
the checks run through the runtime's own ``exec``. Skipped when Docker or the
pack's image is unavailable; labs are always destroyed.
"""

from __future__ import annotations

import select
import socket
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from dns_lab_testdata import FAULT_PARAMS, IMAGE

import domains.dns
from domains.dns.fixtures import READY_PATH
from harness.adapters.docker_lab import DockerLabRuntime
from harness.core.domain_adapter import DetectedCommand
from harness.core.ports import ExecRequest, ExecResult, LabInfo, LabRuntime, LabSpec

docker = pytest.importorskip("docker")

ADAPTER = domains.dns.adapter()
IMAGE_REF = f"{IMAGE.repository}@{IMAGE.digest}"

# Mirrors the checks and reference solution of
# contents/software-engineering/activities/gen-diagnose-dns-resolver-misconfiguration-001*.json.
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
def runtime(docker_client: Any) -> DockerLabRuntime:
    return DockerLabRuntime(docker_client)


@pytest.fixture
def new_id(runtime: DockerLabRuntime) -> Iterator[Callable[[], str]]:
    """Hands out lab ids and destroys every lab they named, even on failure."""
    created: list[str] = []

    def make() -> str:
        lab_id = f"test-dns-{uuid.uuid4().hex[:12]}"
        created.append(lab_id)
        return lab_id

    yield make
    for lab_id in created:
        runtime.destroy(lab_id)


def build_spec() -> LabSpec:
    return ADAPTER.fixtures["resolver_lab"].build_lab_spec(IMAGE, FAULT_PARAMS)


@pytest.fixture
def lab(runtime: DockerLabRuntime, new_id: Callable[[], str]) -> LabInfo:
    return runtime.start(new_id(), build_spec())


def run(runtime: LabRuntime, lab_id: str, *argv: str, timeout: float = 10) -> ExecResult:
    return runtime.exec(
        lab_id,
        ExecRequest(
            argv=argv, timeout_seconds=timeout, max_output_bytes=65536, env={}, workdir=None
        ),
    )


def _run_checks(runtime: LabRuntime, lab_id: str) -> dict[str, bool]:
    return {
        name: ADAPTER.checks[name].run(runtime, lab_id, params).passed for name, params in CHECKS
    }


def test_start_returns_only_once_the_lab_answers(
    runtime: DockerLabRuntime, new_id: Callable[[], str]
) -> None:
    info = runtime.start(new_id(), build_spec())

    # No polling here: the runtime waited for the fixture's readiness probe.
    assert run(runtime, info.lab_instance_id, "test", "-e", READY_PATH).exit_code == 0
    answers = run(
        runtime,
        info.lab_instance_id,
        "dig",
        "+short",
        "+timeout=2",
        "@127.0.0.53",
        "api.corp.internal",
    )
    assert answers.exit_code == 0 and answers.stdout == b"127.0.10.20\n"
    health = run(runtime, info.lab_instance_id, "curl", "-fsS", "http://127.0.10.20:8080/health")
    assert health.exit_code == 0


def test_checks_fail_when_broken_and_pass_after_reference_solution(
    runtime: DockerLabRuntime, lab: LabInfo
) -> None:
    lab_id = lab.lab_instance_id
    assert _run_checks(runtime, lab_id) == {name: False for name, _ in CHECKS}
    assert run(runtime, lab_id, *SOLUTION).exit_code == 0
    assert _run_checks(runtime, lab_id) == {name: True for name, _ in CHECKS}


def test_pinning_in_etc_hosts_does_not_pass_resolver_answers(
    runtime: DockerLabRuntime, lab: LabInfo
) -> None:
    lab_id = lab.lab_instance_id
    pin = ("sh", "-c", "echo '127.0.10.20 api.corp.internal' >> /etc/hosts")
    assert run(runtime, lab_id, *pin).exit_code == 0
    passed = _run_checks(runtime, lab_id)
    assert passed["name_resolves"] and passed["http_status"] and passed["command_exit"]
    assert not passed["resolver_answers"]


def test_reset_restores_a_ready_lab(
    runtime: DockerLabRuntime, lab: LabInfo, new_id: Callable[[], str]
) -> None:
    assert run(runtime, lab.lab_instance_id, *SOLUTION).exit_code == 0

    fresh = runtime.reset(lab.lab_instance_id, new_lab_instance_id=new_id(), spec=build_spec())

    assert fresh.runtime_ref != lab.runtime_ref
    # Ready again, and back to the broken resolver the params describe.
    assert run(runtime, fresh.lab_instance_id, "test", "-e", READY_PATH).exit_code == 0
    assert not _run_checks(runtime, fresh.lab_instance_id)["resolver_answers"]


def test_terminal_reports_commands(docker_client: Any, lab: LabInfo) -> None:
    launch = ADAPTER.tools["terminal"].launch()
    detector = ADAPTER.tools["terminal"].new_command_detector()
    api = docker_client.api
    exec_id = api.exec_create(
        lab.runtime_ref, list(launch.argv), stdin=True, tty=True, environment=dict(launch.env)
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
