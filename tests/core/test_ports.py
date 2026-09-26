"""Tests for the lab, terminal and LLM Ports (harness.core.ports) and their fakes.

Covers value-type invariants (argv safety, digest-pinned images, aware
timestamps) and the fakes' behaviour. The annotated assignments below also let
mypy check that each fake satisfies its Protocol.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from harness.core.ports import (
    ExecRequest,
    ExecResult,
    ImageRef,
    LabFile,
    LabNotFoundError,
    LabRuntime,
    LabSpec,
    LLMMessage,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    ResourceLimits,
    TerminalBridge,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSize,
    format_timestamp,
)
from harness.testing.fakes import (
    FakeLabRuntime,
    FakeLLMProvider,
    FakeTerminalBridge,
)

DIGEST = "sha256:" + "a" * 64
T0 = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


# --- Protocol conformance (checked by mypy) ---------------------------------


def test_fakes_satisfy_their_protocols() -> None:
    llm: LLMProvider = FakeLLMProvider()
    lab: LabRuntime = FakeLabRuntime()
    terminal: TerminalBridge = FakeTerminalBridge()
    assert all(p is not None for p in (llm, lab, terminal))


# --- Value-type invariants ---------------------------------------------------


def test_format_timestamp_normalizes_to_utc_z() -> None:
    jst = timezone(timedelta(hours=9))
    assert format_timestamp(datetime(2026, 9, 22, 19, 0, tzinfo=jst)) == "2026-09-22T10:00:00Z"
    with pytest.raises(ValueError):
        format_timestamp(datetime(2026, 9, 22, 10, 0))


@pytest.mark.parametrize(
    "argv",
    [(), "dig example", ("dig example",), ("",), ("dig", "a\x00b"), ["dig"]],
    ids=["empty", "str", "command-line", "blank", "nul", "list"],
)
def test_exec_request_rejects_unsafe_argv(argv: Any) -> None:
    with pytest.raises(ValueError):
        ExecRequest(argv=argv, timeout_seconds=5, max_output_bytes=1024, env={}, workdir=None)


def test_exec_request_accepts_argv_tuple() -> None:
    request = ExecRequest(
        argv=("dig", "+short", "example"),
        timeout_seconds=5,
        max_output_bytes=1024,
        env={},
        workdir="/workspace",
    )
    assert request.argv[0] == "dig"


@pytest.mark.parametrize(
    "repository, digest",
    [
        ("ghcr.io/org/lab:latest", DIGEST),
        ("ghcr.io/org/lab@sha256", DIGEST),
        ("ghcr.io/org/lab", "latest"),
        ("ghcr.io/org/lab", "sha256:ABC"),
    ],
)
def test_image_ref_requires_digest_and_no_tag(repository: str, digest: str) -> None:
    with pytest.raises(ValueError):
        ImageRef(repository=repository, digest=digest)


def test_lab_file_requires_absolute_contained_path() -> None:
    LabFile(path="/etc/resolv.conf", content=b"", mode=0o644)
    for bad in ("etc/resolv.conf", "/etc/../../host", "/a/.."):
        with pytest.raises(ValueError):
            LabFile(path=bad, content=b"", mode=0o644)


def test_llm_request_needs_messages() -> None:
    provenance = LLMProvenance(
        provider="example", model="m", prompt_id="p", prompt_version="1", generation_parameters={}
    )
    with pytest.raises(ValueError):
        LLMRequest(
            role="tutor", llm=provenance, messages=[], output_schema_id="x", output_schema={}
        )


# --- Other fakes -------------------------------------------------------------


def _lab_spec() -> LabSpec:
    return LabSpec(
        image=ImageRef(repository="ghcr.io/example/lab", digest=DIGEST),
        limits=ResourceLimits(cpus=0.5, memory_bytes=256 * 2**20, pids=128, lifetime_seconds=3600),
        network="none",
        env={},
        files=[LabFile(path="/etc/motd", content=b"hi\n", mode=0o644)],
        workdir=None,
    )


def test_fake_lab_runtime_lifecycle() -> None:
    def handler(lab_id: str, request: ExecRequest) -> ExecResult:
        return ExecResult(
            exit_code=1,
            stdout=b"",
            stderr=b"fail",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    lab = FakeLabRuntime(exec_handler=handler)
    info = lab.start("lab_01", _lab_spec())
    assert info.image_digest == DIGEST and lab.status("lab_01") == "running"
    check = ExecRequest(
        argv=("true",), timeout_seconds=1, max_output_bytes=64, env={}, workdir=None
    )
    assert lab.exec("lab_01", check).exit_code == 1
    new = lab.reset("lab_01", new_lab_instance_id="lab_02", spec=_lab_spec())
    assert new.lab_instance_id == "lab_02" and lab.status("lab_01") == "absent"
    lab.destroy("lab_02")
    lab.destroy("lab_02")
    with pytest.raises(LabNotFoundError):
        lab.exec("lab_02", check)


def test_fake_terminal_bridge_streams_bytes() -> None:
    async def scenario() -> tuple[list[bytes], int | None]:
        bridge = FakeTerminalBridge()
        session = await bridge.open(
            TerminalOpenRequest(
                lab_instance_id="lab_01",
                runtime_ref="fake-lab_01",
                terminal_id="term_01",
                argv=("/bin/sh",),
                size=TerminalSize(cols=80, rows=24),
                env={},
                workdir=None,
            )
        )
        await session.write(b"ls\r")
        await session.resize(TerminalSize(cols=120, rows=40))
        await session.close()
        chunks = [chunk async for chunk in session.output()]
        with pytest.raises(TerminalClosedError):
            await session.write(b"x")
        return chunks, await session.wait()

    assert asyncio.run(scenario()) == ([b"ls\r"], None)


def test_fake_llm_provider_echoes_request_settings_as_provenance() -> None:
    provenance = LLMProvenance(
        provider="example",
        model="m",
        prompt_id="tutor-reply",
        prompt_version="1",
        generation_parameters={"temperature": 0.3},
    )
    llm = FakeLLMProvider([{"text": "hi"}])
    response = llm.complete_structured(
        LLMRequest(
            role="tutor",
            llm=provenance,
            messages=[LLMMessage(role="user", content="help")],
            output_schema_id="https://morphloop.dev/contracts/schemas/llm/tutor.reply/1.json",
            output_schema={"type": "object"},
        )
    )
    assert response.output == {"text": "hi"} and response.provenance == provenance
