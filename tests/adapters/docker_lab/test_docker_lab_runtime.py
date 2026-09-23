"""Real-Docker tests for :class:`harness.adapters.docker_lab.DockerLabRuntime`.

Skipped when no Docker daemon is reachable. Every lab id a test creates is
cleaned up afterwards, even on failure, by label.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator

import docker
import docker.errors
import pytest

from harness.adapters.docker_lab import DockerLabRuntime
from harness.adapters.docker_lab.runtime import LABEL_LAB_ID, LABEL_MANAGED
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    ImageRef,
    LabFile,
    LabNotFoundError,
    LabNotReadyError,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    NetworkMode,
    ReadinessProbe,
    ResourceLimits,
)

BUSYBOX = ImageRef(
    repository="busybox",
    digest="sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662",
)
MIB = 1024 * 1024


@pytest.fixture(scope="module")
def client() -> Iterator[docker.DockerClient]:
    try:
        c = docker.from_env()
        c.ping()
    except docker.errors.DockerException as exc:
        pytest.skip(f"Docker is not available: {exc}")
    try:
        c.images.get(f"{BUSYBOX.repository}@{BUSYBOX.digest}")
    except docker.errors.ImageNotFound:
        c.images.pull(BUSYBOX.repository, tag=BUSYBOX.digest)
    yield c
    c.close()


@pytest.fixture
def runtime(client: docker.DockerClient) -> DockerLabRuntime:
    return DockerLabRuntime(client)


@pytest.fixture
def new_id(client: docker.DockerClient) -> Iterator[Callable[[], str]]:
    created: list[str] = []

    def make() -> str:
        lab_id = f"test-{uuid.uuid4().hex[:12]}"
        created.append(lab_id)
        return lab_id

    yield make
    for lab_id in created:
        filters: dict[str, str | list[str] | bool] = {
            "label": [f"{LABEL_MANAGED}=docker_lab", f"{LABEL_LAB_ID}={lab_id}"]
        }
        for container in client.containers.list(all=True, filters=filters):
            container.remove(force=True, v=True)
        for network in client.networks.list(filters=filters):
            network.remove()


def make_spec(
    *,
    network: NetworkMode = "isolated",
    files: tuple[LabFile, ...] = (),
    memory_bytes: int = 64 * MIB,
    lifetime_seconds: int = 600,
    command: tuple[str, ...] | None = None,
    readiness: ReadinessProbe | None = None,
) -> LabSpec:
    return LabSpec(
        image=BUSYBOX,
        limits=ResourceLimits(
            cpus=0.5, memory_bytes=memory_bytes, pids=64, lifetime_seconds=lifetime_seconds
        ),
        network=network,
        env={"LAB_GREETING": "hello"},
        files=files,
        workdir="/work",
        command=command,
        readiness=readiness,
    )


def run(
    rt: LabRuntime,
    lab_id: str,
    *argv: str,
    timeout: float = 10,
    max_bytes: int = 65536,
    env: dict[str, str] | None = None,
    workdir: str | None = None,
) -> ExecResult:
    return rt.exec(
        lab_id,
        ExecRequest(
            argv=argv,
            timeout_seconds=timeout,
            max_output_bytes=max_bytes,
            env=env or {},
            workdir=workdir,
        ),
    )


def test_satisfies_port(runtime: DockerLabRuntime) -> None:
    rt: LabRuntime = runtime
    assert rt is runtime


def test_start_applies_security_settings_and_limits(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    info = runtime.start(lab_id, make_spec())

    assert info.lab_instance_id == lab_id
    assert info.status == "running"
    assert info.image_digest == BUSYBOX.digest
    assert runtime.status(lab_id) == "running"

    attrs = client.containers.get(info.runtime_ref).attrs
    assert attrs["Id"] == info.runtime_ref
    assert attrs["Config"]["Image"] == f"{BUSYBOX.repository}@{BUSYBOX.digest}"
    assert attrs["Mounts"] == []
    host = attrs["HostConfig"]
    assert not host["Binds"]
    assert not host.get("Mounts")
    assert host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"]
    assert not host["CapAdd"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert not host.get("Devices")
    assert host["NetworkMode"] != "host"
    assert host["PidMode"] == "" and host["IpcMode"] == "private"
    assert host["NanoCpus"] == 500_000_000
    assert host["Memory"] == 64 * MIB and host["MemorySwap"] == 64 * MIB
    assert host["PidsLimit"] == 64

    # Inside the lab: no effective capabilities, no Docker socket, env and workdir applied.
    status = run(runtime, lab_id, "cat", "/proc/self/status").stdout.decode()
    cap_eff = next(line for line in status.splitlines() if line.startswith("CapEff:"))
    assert int(cap_eff.split()[1], 16) == 0
    assert run(runtime, lab_id, "test", "-e", "/var/run/docker.sock").exit_code == 1
    assert run(runtime, lab_id, "test", "-e", "/run/docker.sock").exit_code == 1
    assert run(runtime, lab_id, "printenv", "LAB_GREETING").stdout == b"hello\n"
    assert run(runtime, lab_id, "pwd").stdout == b"/work\n"


def test_files_are_copied_with_mode(runtime: DockerLabRuntime, new_id: Callable[[], str]) -> None:
    lab_id = new_id()
    files = (
        LabFile(path="/etc/lab/nested/conf.txt", content=b"a=1\n", mode=0o640),
        LabFile(path="/usr/local/bin/hello", content=b"#!/bin/sh\necho hi\n", mode=0o755),
    )
    runtime.start(lab_id, make_spec(files=files))

    assert run(runtime, lab_id, "cat", "/etc/lab/nested/conf.txt").stdout == b"a=1\n"
    assert run(runtime, lab_id, "stat", "-c", "%a", "/etc/lab/nested/conf.txt").stdout == b"640\n"
    assert run(runtime, lab_id, "/usr/local/bin/hello").stdout == b"hi\n"


def test_declared_command_is_the_labs_main_process(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    command = ("sh", "-c", "echo booted > /work/boot.log; exec sleep 300")
    info = runtime.start(lab_id, make_spec(network="none", command=command))

    assert client.containers.get(info.runtime_ref).attrs["Config"]["Cmd"] == list(command)
    assert run(runtime, lab_id, "cat", "/work/boot.log").stdout == b"booted\n"
    # The declared argv replaced the image default and is PID 1 in the lab.
    assert b"sleep\x00300\x00" == run(runtime, lab_id, "cat", "/proc/1/cmdline").stdout


def test_start_returns_only_after_the_readiness_probe_succeeds(
    runtime: DockerLabRuntime, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    spec = make_spec(
        network="none",
        command=("sh", "-c", "sleep 2; touch /work/ready; exec sleep 300"),
        readiness=ReadinessProbe(
            argv=("test", "-e", "/work/ready"), timeout_seconds=30, interval_seconds=0.1
        ),
    )

    started = time.monotonic()
    runtime.start(lab_id, spec)
    elapsed = time.monotonic() - started

    # start() waited for the lab's own service, not just for the container.
    assert elapsed >= 2
    assert run(runtime, lab_id, "test", "-e", "/work/ready").exit_code == 0


def test_lab_that_never_becomes_ready_fails_the_start_and_is_cleaned_up(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    spec = make_spec(
        network="none",
        command=("sleep", "300"),
        readiness=ReadinessProbe(
            argv=("test", "-e", "/work/never"), timeout_seconds=1, interval_seconds=0.1
        ),
    )

    started = time.monotonic()
    with pytest.raises(LabNotReadyError, match="not ready"):
        runtime.start(lab_id, spec)

    assert time.monotonic() - started < 15
    assert runtime.status(lab_id) == "absent"
    filters: dict[str, str | list[str] | bool] = {"label": f"{LABEL_LAB_ID}={lab_id}"}
    assert client.containers.list(all=True, filters=filters) == []


def test_main_process_that_stops_before_ready_fails_the_start(
    runtime: DockerLabRuntime, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    spec = make_spec(
        network="none",
        command=("sh", "-c", "sleep 1; exit 3"),
        readiness=ReadinessProbe(
            argv=("test", "-e", "/work/never"), timeout_seconds=20, interval_seconds=0.2
        ),
    )

    with pytest.raises(LabRuntimeError, match="ready"):
        runtime.start(lab_id, spec)
    assert runtime.status(lab_id) == "absent"


def test_exec_results(runtime: DockerLabRuntime, new_id: Callable[[], str]) -> None:
    lab_id = new_id()
    runtime.start(lab_id, make_spec(network="none"))

    ok = run(runtime, lab_id, "echo", "out")
    assert (ok.exit_code, ok.stdout, ok.stderr, ok.timed_out) == (0, b"out\n", b"", False)

    # argv is not a shell: metacharacters are passed literally.
    literal = run(runtime, lab_id, "echo", "$HOME; rm -rf /")
    assert literal.stdout == b"$HOME; rm -rf /\n"

    failed = run(runtime, lab_id, "sh", "-c", "echo err >&2; exit 3", env={"X": "1"}, workdir="/")
    assert (failed.exit_code, failed.stdout, failed.stderr) == (3, b"", b"err\n")

    env = run(runtime, lab_id, "printenv", "X", env={"X": "from-request"})
    assert env.stdout == b"from-request\n"

    big = run(runtime, lab_id, "head", "-c", "5000", "/dev/zero", max_bytes=100)
    assert big.exit_code == 0 and len(big.stdout) == 100 and big.stdout_truncated
    assert not big.stderr_truncated

    missing = run(runtime, lab_id, "/no/such/binary")
    assert missing.exit_code not in (0, None) and not missing.timed_out


def test_exec_timeout_kills_the_command(
    runtime: DockerLabRuntime, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    runtime.start(lab_id, make_spec(network="none"))

    started = time.monotonic()
    result = run(runtime, lab_id, "sh", "-c", "sleep 300 & sleep 301", timeout=1)
    assert time.monotonic() - started < 10
    assert result.timed_out and result.exit_code is None

    ps = run(runtime, lab_id, "ps", "-o", "args").stdout.decode()
    assert "sleep 300" not in ps and "sleep 301" not in ps


def test_network_none_has_loopback_only(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    info = runtime.start(lab_id, make_spec(network="none"))

    assert client.containers.get(info.runtime_ref).attrs["HostConfig"]["NetworkMode"] == "none"
    links = run(runtime, lab_id, "ip", "-o", "link").stdout.decode()
    assert "eth0" not in links


def test_isolated_network_is_private_internal_and_unreachable(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_a, lab_b = new_id(), new_id()
    info_a = runtime.start(lab_a, make_spec())
    runtime.start(lab_b, make_spec())

    networks = client.containers.get(info_a.runtime_ref).attrs["NetworkSettings"]["Networks"]
    assert len(networks) == 1
    (net_name,) = networks
    network = client.networks.get(net_name)
    assert network.attrs["Internal"] is True
    assert network.attrs["Options"]["com.docker.network.bridge.inhibit_ipv4"] == "true"
    assert list(network.attrs["Containers"]) == [info_a.runtime_ref]

    # No default route and no egress.
    assert "default" not in run(runtime, lab_a, "ip", "route").stdout.decode()
    egress = run(runtime, lab_a, "wget", "-T", "2", "-q", "-O-", "http://1.1.1.1/", timeout=10)
    assert egress.exit_code != 0

    # Lab B cannot reach lab A.
    ip_a = networks[net_name]["IPAddress"]
    assert run(runtime, lab_a, "sh", "-c", "nc -l -p 8080 >/dev/null &").exit_code == 0
    probe = run(runtime, lab_b, "nc", "-w", "2", ip_a, "8080", timeout=10)
    assert probe.exit_code != 0

    runtime.destroy(lab_a)
    with pytest.raises(docker.errors.NotFound):
        client.networks.get(str(network.id))


def test_reset_starts_a_fresh_lab(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    old_id, new = new_id(), new_id()
    old = runtime.start(old_id, make_spec())
    assert run(runtime, old_id, "touch", "/work/leftover").exit_code == 0

    fresh = runtime.reset(old_id, new_lab_instance_id=new, spec=make_spec())

    assert fresh.lab_instance_id == new and fresh.runtime_ref != old.runtime_ref
    assert runtime.status(old_id) == "absent"
    assert runtime.status(new) == "running"
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(old.runtime_ref)
    assert run(runtime, new, "test", "-e", "/work/leftover").exit_code == 1


def test_destroy_is_idempotent_and_lab_is_gone(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    info = runtime.start(lab_id, make_spec())

    runtime.destroy(lab_id)
    runtime.destroy(lab_id)
    runtime.destroy(new_id())

    assert runtime.status(lab_id) == "absent"
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(info.runtime_ref)
    filters: dict[str, str | list[str] | bool] = {"label": f"{LABEL_LAB_ID}={lab_id}"}
    assert client.networks.list(filters=filters) == []
    with pytest.raises(LabNotFoundError):
        run(runtime, lab_id, "true")


def test_duplicate_id_is_rejected(runtime: DockerLabRuntime, new_id: Callable[[], str]) -> None:
    lab_id = new_id()
    runtime.start(lab_id, make_spec(network="none"))
    with pytest.raises(LabRuntimeError):
        runtime.start(lab_id, make_spec(network="none"))
    assert runtime.status(lab_id) == "running"


def test_failed_start_is_cleaned_up(
    runtime: DockerLabRuntime, client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    # The Engine rejects a memory limit this small after the network was created.
    with pytest.raises(LabRuntimeError):
        runtime.start(lab_id, make_spec(memory_bytes=1024))

    assert runtime.status(lab_id) == "absent"
    filters: dict[str, str | list[str] | bool] = {"label": f"{LABEL_LAB_ID}={lab_id}"}
    assert client.containers.list(all=True, filters=filters) == []
    assert client.networks.list(filters=filters) == []


def test_lifetime_is_enforced(runtime: DockerLabRuntime, new_id: Callable[[], str]) -> None:
    lab_id = new_id()
    runtime.start(lab_id, make_spec(network="none", lifetime_seconds=1))

    deadline = time.monotonic() + 15
    while runtime.status(lab_id) != "absent" and time.monotonic() < deadline:
        time.sleep(0.2)
    assert runtime.status(lab_id) == "absent"


def test_reap_expired_removes_labs_left_by_another_process(
    client: docker.DockerClient, new_id: Callable[[], str]
) -> None:
    lab_id = new_id()
    previous = DockerLabRuntime(client)
    previous.start(lab_id, make_spec(lifetime_seconds=1))
    # Simulate the previous process dying: its timers never fire.
    for timer in previous._timers.values():
        timer.cancel()

    time.sleep(1.5)
    assert lab_id in DockerLabRuntime(client).reap_expired()
    assert previous.status(lab_id) == "absent"
