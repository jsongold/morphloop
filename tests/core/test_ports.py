"""Tests for the v0.1 Ports (harness.core.ports) and their in-memory fakes.

Covers: Port value types round-trip against `contracts/` (ADR-0017 contract
tests), value-type invariants (argv safety, digest-pinned images, aware
timestamps), and the event store contract (position order, idempotent append
AC-F5, atomic commit/rollback ADR-0008, rebuild reads AC-F6) on the in-memory
fake. The annotated assignments below also let mypy check that each fake
satisfies its Protocol.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from harness.core.ports import (
    AppendRequest,
    EventStore,
    EventStoreError,
    ExecRequest,
    ExecResult,
    IdempotencyConflictError,
    ImageRef,
    LabFile,
    LabNotFoundError,
    LabRuntime,
    LabSpec,
    LLMMessage,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    PackFileNotFoundError,
    PackLocationNotFoundError,
    PackPathError,
    PackSource,
    ResourceLimits,
    StoredEvent,
    TerminalBridge,
    TerminalClosedError,
    TerminalOpenRequest,
    TerminalSize,
    check_pack_path,
    format_timestamp,
)
from harness.testing.contracts import ContractViolation, validate
from harness.testing.fakes import (
    FakeLabRuntime,
    FakeLLMProvider,
    FakeTerminalBridge,
    InMemoryEventStore,
    InMemoryPackSource,
)

FIXTURES = Path(__file__).resolve().parent.parent / "contracts" / "fixtures" / "events" / "valid"
VALID_EVENTS = sorted(FIXTURES.glob("*.json"))
APPEND = "schemas/events/envelope/append.json"
STORED = "schemas/events/envelope/stored.json"
DIGEST = "sha256:" + "a" * 64
T0 = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _append_request_from(event: dict[str, Any], **overrides: Any) -> AppendRequest:
    fields = {k: v for k, v in event.items() if k not in ("position", "recorded_at")}
    fields["occurred_at"] = _ts(fields["occurred_at"])
    fields.update(overrides)
    return AppendRequest(**fields)


def _command_request(
    n: int, *, session_id: str = "ses_01", key: str | None = None
) -> AppendRequest:
    return AppendRequest(
        event_id=f"evt_{n:04d}",
        event_type="terminal.command",
        event_version=1,
        occurred_at=T0 + timedelta(seconds=n),
        idempotency_key=key if key is not None else f"client:terminal.command:{n}",
        causation_id=None,
        correlation_id="att_01",
        learner_id="usr_01",
        session_id=session_id,
        attempt_id="att_01",
        activity_definition_id="some-activity-v1",
        actor="learner",
        payload={
            "terminal_id": "term_01",
            "lab_instance_id": "lab_01",
            "sequence": n,
            "command": f"echo {n}",
            "cwd": "/workspace",
        },
    )


# --- Protocol conformance (checked by mypy) ---------------------------------


def test_fakes_satisfy_their_protocols() -> None:
    store: EventStore = InMemoryEventStore()
    llm: LLMProvider = FakeLLMProvider()
    lab: LabRuntime = FakeLabRuntime()
    terminal: TerminalBridge = FakeTerminalBridge()
    pack: PackSource = InMemoryPackSource({})
    assert all(p is not None for p in (store, llm, lab, terminal, pack))


# --- Contract round trips ----------------------------------------------------


@pytest.mark.parametrize("path", VALID_EVENTS, ids=lambda p: p.name)
def test_append_request_round_trips_against_contract(path: Path) -> None:
    event = _load(path)
    request = _append_request_from(event)
    wire = request.to_dict()
    validate(wire, APPEND)
    assert wire == {k: v for k, v in event.items() if k not in ("position", "recorded_at")}


@pytest.mark.parametrize("path", VALID_EVENTS, ids=lambda p: p.name)
def test_stored_event_from_fake_store_validates_against_contract(path: Path) -> None:
    store = InMemoryEventStore(now=lambda: T0 + timedelta(microseconds=123000))
    with store.transaction() as tx:
        result = tx.append(_append_request_from(_load(path)))
    validate(result.event.to_dict(), STORED)
    assert result.event.to_dict()["recorded_at"] == "2026-09-22T10:00:00.123000Z"


def test_append_request_to_dict_rejects_db_assigned_fields_in_contract() -> None:
    wire = _command_request(1).to_dict()
    wire["position"] = 1
    with pytest.raises(ContractViolation):
        validate(wire, APPEND)


def test_llm_provenance_embeds_into_learner_skill_updated_event() -> None:
    event = _load(FIXTURES / "learner_skill.updated.v1.json")
    provenance = LLMProvenance(
        provider="example",
        model="model-x",
        prompt_id="learner-model-update",
        prompt_version="2",
        generation_parameters={"temperature": 0.0, "max_tokens": 512, "seed": None},
    )
    event["payload"]["provenance"] = provenance.to_dict()
    validate(event, STORED)


def test_format_timestamp_normalizes_to_utc_z() -> None:
    jst = timezone(timedelta(hours=9))
    assert format_timestamp(datetime(2026, 9, 22, 19, 0, tzinfo=jst)) == "2026-09-22T10:00:00Z"
    with pytest.raises(ValueError):
        format_timestamp(datetime(2026, 9, 22, 10, 0))


# --- Value-type invariants ---------------------------------------------------


def test_envelope_rejects_naive_time_and_unpaired_attempt() -> None:
    with pytest.raises(ValueError):
        _append_request_from(_command_request(1).to_dict(), occurred_at=datetime(2026, 9, 22))
    with pytest.raises(ValueError):
        _append_request_from(_command_request(1).to_dict(), activity_definition_id=None)


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


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "a//b", "./a", "a\\b", "a/."])
def test_check_pack_path_rejects_non_relative_posix(path: str) -> None:
    with pytest.raises(PackPathError):
        check_pack_path(path)


# --- Event store behaviour (in-memory fake) ----------------------------------


def test_append_assigns_increasing_positions_and_reads_in_order() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        for n in range(1, 4):
            tx.append(_command_request(n))
        tx.append(_command_request(4, session_id="ses_02"))
    all_events = store.read_all()
    assert [e.position for e in all_events] == [1, 2, 3, 4]
    session = store.read_session("ses_01", after_position=1, until_position=3)
    assert [e.event_id for e in session] == ["evt_0002", "evt_0003"]
    assert [e.position for e in store.read_all(after_position=2, limit=1)] == [3]


def test_idempotent_resend_returns_existing_event_without_new_one() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        first = tx.append(_command_request(1))
    resend = _append_request_from(
        _command_request(1).to_dict(), event_id="evt_9999", occurred_at=T0 + timedelta(hours=1)
    )
    with store.transaction() as tx:
        second = tx.append(resend)
    assert first.created and not second.created
    assert second.event == first.event
    assert len(store.read_all()) == 1


def test_reused_idempotency_key_with_different_content_conflicts() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        tx.append(_command_request(1, key="k-1"))
    with pytest.raises(IdempotencyConflictError) as excinfo, store.transaction() as tx:
        tx.append(_command_request(2, key="k-1"))
    assert excinfo.value.existing.event_id == "evt_0001"


def test_duplicate_event_id_is_rejected() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        tx.append(_command_request(1, key="k-1"))
    with pytest.raises(EventStoreError), store.transaction() as tx:
        tx.append(_command_request(1, key="k-2"))


def test_rollback_discards_event_and_projection_together() -> None:
    store = InMemoryEventStore()
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.append(_command_request(1))
        tx.put_projection("commands", "usr_01", {"count": 1})
        raise RuntimeError("projection update failed")
    assert store.read_all() == []
    with store.transaction() as tx:
        assert tx.get_projection("commands", "usr_01") is None


def test_transaction_object_is_unusable_after_its_block() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        pass
    with pytest.raises(EventStoreError):
        tx.append(_command_request(1))


def test_projection_access_and_rebuild_from_log() -> None:
    store = InMemoryEventStore()

    def apply(tx_: Any, event: StoredEvent) -> None:
        key = f"{event.learner_id}/{event.session_id}"
        current = tx_.get_projection("commands", key) or {"count": 0}
        tx_.put_projection("commands", key, {"count": current["count"] + 1})

    with store.transaction() as tx:
        tx.lock_learner("usr_01")
        for n in range(1, 4):
            result = tx.append(_command_request(n))
            if result.created:
                apply(tx, result.event)
        live = list(tx.list_projection("commands", key_prefix="usr_01/"))

    with store.transaction() as tx:
        tx.clear_projection("commands")
        assert tx.list_projection("commands") == []
        after = 0
        while page := store.read_all(after_position=after, limit=2):
            for event in page:
                apply(tx, event)
            after = page[-1].position
        rebuilt = list(tx.list_projection("commands"))

    assert rebuilt == live == [("usr_01/ses_01", {"count": 3})]


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


def test_in_memory_pack_source_lists_sorted_and_reads_bytes() -> None:
    source = InMemoryPackSource({"/packs/p": {"skills/b.yaml": b"b", "manifest.yaml": b"m"}})
    assert source.list_files("/packs/p") == ["manifest.yaml", "skills/b.yaml"]
    assert source.read_bytes("/packs/p", "manifest.yaml") == b"m"
    with pytest.raises(PackFileNotFoundError):
        source.read_bytes("/packs/p", "missing.yaml")
    with pytest.raises(PackPathError):
        source.read_bytes("/packs/p", "../etc/passwd")
    with pytest.raises(PackLocationNotFoundError):
        source.list_files("/nowhere")


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
