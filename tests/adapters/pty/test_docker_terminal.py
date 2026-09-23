"""Integration tests for the Docker PTY TerminalBridge (harness.adapters.pty).

Each test runs against a throwaway container (``sleep infinity``, no mounts, no
network) started directly with the Docker SDK and always removed afterwards.
An image already present locally is preferred; otherwise busybox is pulled. The
module is skipped when Docker is unavailable or the pull fails.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from harness.adapters.pty import DockerTerminalBridge
from harness.core.ports import (
    TerminalBridge,
    TerminalBridgeError,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
)

docker = pytest.importorskip("docker")

_CANDIDATE_IMAGES = ("busybox:latest", "alpine:latest", "python:3.13-slim")
_TIMEOUT = 15.0


@pytest.fixture(scope="module")
def docker_client() -> Iterator[Any]:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "no Docker here"
        pytest.skip(f"Docker unavailable: {exc}")
    yield client
    client.close()


@pytest.fixture(scope="module")
def lab_image(docker_client: Any) -> str:
    for ref in _CANDIDATE_IMAGES:
        try:
            docker_client.images.get(ref)
        except docker.errors.ImageNotFound:
            continue
        return ref
    try:
        docker_client.images.pull("busybox", tag="latest")
    except Exception as exc:  # noqa: BLE001 - no image means "cannot run here"
        pytest.skip(f"none of {_CANDIDATE_IMAGES} is available: {exc}")
    return "busybox:latest"


@pytest.fixture
def lab_container(docker_client: Any, lab_image: str) -> Iterator[Any]:
    container = docker_client.containers.run(
        lab_image,
        ["sleep", "infinity"],
        detach=True,
        network_mode="none",
        name=f"morphloop-pty-test-{uuid.uuid4().hex[:12]}",
        labels={"morphloop.test": "adapters-pty"},
    )
    try:
        yield container
    finally:
        container.remove(force=True)


def _request(container_id: str, **overrides: Any) -> TerminalOpenRequest:
    fields: dict[str, Any] = {
        "lab_instance_id": "lab-1",
        "runtime_ref": container_id,
        "terminal_id": "term-1",
        "argv": ("/bin/sh",),
        "size": TerminalSize(cols=80, rows=24),
        "env": {"PTY_TEST_VAR": "from-env"},
        "workdir": "/tmp",
    }
    fields.update(overrides)
    return TerminalOpenRequest(**fields)


async def _read_until(stream: AsyncIterator[bytes], needle: bytes, seen: bytearray) -> None:
    async def loop() -> None:
        while needle not in seen:
            seen.extend(await anext(stream))

    await asyncio.wait_for(loop(), _TIMEOUT)


async def _drain(stream: AsyncIterator[bytes]) -> None:
    async for _ in stream:
        pass


@pytest_asyncio.fixture
async def session(lab_container: Any) -> AsyncIterator[TerminalSession]:
    bridge: TerminalBridge = DockerTerminalBridge()
    opened = await bridge.open(_request(lab_container.id))
    try:
        yield opened
    finally:
        await opened.close()


@pytest.mark.asyncio
async def test_echo_resize_exit_and_close(session: TerminalSession) -> None:
    assert session.terminal_id == "term-1"
    stream = session.output()
    seen = bytearray()

    await session.write(b'echo hel""lo\n')  # echoed input is not a match
    await _read_until(stream, b"hello\r\n", seen)

    await session.write(b'echo "var=$PTY_TEST_VAR dir=$(pwd)"\n')
    await _read_until(stream, b"var=from-env dir=/tmp", seen)

    await session.write(b"stty size\n")
    await _read_until(stream, b"24 80", seen)
    await session.resize(TerminalSize(cols=132, rows=40))
    await session.write(b"stty size\n")
    await _read_until(stream, b"40 132", seen)

    await session.write(b"exit 0\n")
    await asyncio.wait_for(_drain(stream), _TIMEOUT)  # output ends when the process exits
    assert await asyncio.wait_for(session.wait(), _TIMEOUT) == 0

    await session.close()
    await session.close()  # idempotent
    with pytest.raises(TerminalClosedError):
        await session.write(b"echo late\n")


@pytest.mark.asyncio
async def test_nonzero_exit_code(session: TerminalSession) -> None:
    await session.write(b"exit 3\n")
    assert await asyncio.wait_for(session.wait(), _TIMEOUT) == 3


@pytest.mark.asyncio
async def test_close_terminates_process_and_ends_output(
    session: TerminalSession, docker_client: Any
) -> None:
    stream = session.output()
    seen = bytearray()
    await session.write(b"echo ready\n")
    await _read_until(stream, b"ready\r\n", seen)

    await session.close()
    await session.close()

    await asyncio.wait_for(_drain(stream), _TIMEOUT)  # output ends on close
    assert await asyncio.wait_for(session.wait(), _TIMEOUT) is None
    with pytest.raises(TerminalClosedError):
        await session.write(b"x")
    exec_id = session._exec_id  # type: ignore[attr-defined]
    assert docker_client.api.exec_inspect(exec_id)["Running"] is False


@pytest.mark.asyncio
async def test_open_on_stopped_lab_fails(lab_container: Any) -> None:
    lab_container.kill()
    with pytest.raises(TerminalBridgeError):
        await DockerTerminalBridge().open(_request(lab_container.id))


@pytest.mark.asyncio
async def test_open_on_missing_lab_fails(docker_client: Any) -> None:
    with pytest.raises(TerminalBridgeError):
        await DockerTerminalBridge(docker_client).open(
            _request(f"morphloop-pty-test-missing-{uuid.uuid4().hex}")
        )
