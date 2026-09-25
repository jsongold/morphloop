"""LabArtifact lifecycle over the SDK Ports (#62): start, reset, stop, check, idle stop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from lab_fixture import (
    SESSION_ID,
    SPEC,
    SPEC_ID,
    SPECS,
    USER_ID,
    WS_ID,
    LabFixture,
    build,
    new_key,
    start,
)

from harness.sdk import ContractSchemas, ExecRequest, ExecResult, artifact_class
from swe.artifacts.lab import ArtifactError, ArtifactView, LabArtifact


def _lab_instance(document: Any) -> str:
    return str(document["lab"]["lab_instance_id"])


def _status(f: LabFixture, call: Callable[..., object]) -> int:
    with pytest.raises(ArtifactError) as err, f.tx() as tx:
        call(tx)
    return err.value.status


def test_lab_is_a_registered_artifact_needing_a_terminal() -> None:
    assert artifact_class("lab") is LabArtifact
    assert LabArtifact.capabilities == frozenset({"terminal"})
    artifact = LabArtifact.from_spec("art_1", SPEC)
    assert artifact.allowed_checks == frozenset({"fake.exit"}) and artifact.idle_seconds == 60


def test_spec_whose_fixture_is_not_allowed_is_refused() -> None:
    bad = {**SPEC, "spec": {**SPEC["spec"], "allowed_fixtures": ["fake.other"]}}
    with pytest.raises(ValueError, match="allowed_fixtures"):
        LabArtifact.from_spec("art_1", bad)
    problems = list(LabArtifact.validate_spec(bad["spec"], pack=None))  # type: ignore[arg-type]
    assert problems == ["environment fixture 'fake.lab' is not in allowed_fixtures"]


def test_lab_spec_schema_bounds_the_generator() -> None:
    schemas = ContractSchemas.load()
    assert schemas.errors_against(SPEC["spec"], LabArtifact.spec_schema) == []
    unbounded = {"environment": SPEC["spec"]["environment"]}
    assert schemas.errors_against(unbounded, LabArtifact.spec_schema) == [
        "$: 'allowed_fixtures' is a required property",
        "$: 'allowed_checks' is a required property",
    ]


def test_start_reset_stop() -> None:
    f = build()
    artifact_id = start(f)
    with f.tx() as tx:
        started = f.service.get(tx, artifact_id, user_id=USER_ID, ws_id=WS_ID)
    first = _lab_instance(started)
    assert started["status"] == "running" and first in f.labs.labs
    assert started["session_id"] == SESSION_ID

    with f.tx() as tx:
        reset = f.service.reset(
            tx, SPECS, artifact_id, event_id=new_key(), user_id=USER_ID, ws_id=WS_ID
        )
    second = _lab_instance(reset)
    assert second != first and list(f.labs.labs) == [second]

    with f.tx() as tx:
        stopped = f.service.stop(tx, artifact_id, event_id=new_key(), user_id=USER_ID, ws_id=WS_ID)
    assert stopped["status"] == "stopped" and f.labs.labs == {}
    assert f.types()[1:] == ["artifact.started", "artifact.reset", "artifact.stopped"]
    assert (
        _status(
            f,
            lambda tx: f.service.reset(
                tx, SPECS, artifact_id, event_id=new_key(), user_id=USER_ID, ws_id=WS_ID
            ),
        )
        == 409
    )


def test_resent_event_id_does_not_act_twice() -> None:
    f = build()
    key = new_key()

    def start_with(key: str) -> Any:
        with f.tx() as tx:
            return f.service.start(
                tx,
                SPECS,
                user_id=USER_ID,
                session_id=SESSION_ID,
                ws_id=WS_ID,
                spec_id=SPEC_ID,
                event_id=key,
            )

    first = start_with(key)
    assert start_with(key) == first and len(f.labs.labs) == 1
    artifact_id = str(first["artifact_id"])
    stop_key = new_key()
    with f.tx() as tx:
        f.service.stop(tx, artifact_id, event_id=stop_key, user_id=USER_ID, ws_id=WS_ID)
    with f.tx() as tx:
        resent = f.service.stop(tx, artifact_id, event_id=stop_key, user_id=USER_ID, ws_id=WS_ID)
    assert resent["status"] == "stopped"
    assert (
        _status(
            f,
            lambda tx: f.service.stop(tx, artifact_id, event_id=key, user_id=USER_ID, ws_id=WS_ID),
        )
        == 409
    )


def test_view_rebuilds_from_events() -> None:
    f = build()
    artifact_id = start(f)
    with f.tx() as tx:
        f.service.stop(tx, artifact_id, event_id=new_key(), user_id=USER_ID, ws_id=WS_ID)
    events = f.store.read()
    with f.tx() as tx:
        before = ArtifactView.get(tx, artifact_id)
        ArtifactView.rebuild(events, tx)
        assert ArtifactView.get(tx, artifact_id) == before


def test_unknown_spec_artifact_ws_and_user() -> None:
    f = build()
    with pytest.raises(ArtifactError) as err, f.tx() as tx:
        f.service.start(
            tx,
            SPECS,
            user_id=USER_ID,
            session_id=SESSION_ID,
            ws_id=WS_ID,
            spec_id="nope",
            event_id=new_key(),
        )
    assert err.value.status == 404
    assert _status(f, lambda tx: f.service.get(tx, "art_nope")) == 404
    artifact_id = start(f)
    assert _status(f, lambda tx: f.service.get(tx, artifact_id, ws_id="ws_other")) == 404
    assert _status(f, lambda tx: f.service.get(tx, artifact_id, user_id="usr_other")) == 404


def test_list_is_type_neutral_ordered_and_scoped() -> None:
    f = build()
    first = start(f)
    second = start(f)
    with f.tx() as tx:
        f.service.stop(tx, first, event_id=new_key(), user_id=USER_ID, ws_id=WS_ID)
    with f.tx() as tx:
        items = f.service.list_artifacts(tx, user_id=USER_ID, ws_id=WS_ID)
    assert items == [
        {"artifact_id": first, "type": "lab", "spec_id": SPEC_ID, "status": "stopped"},
        {"artifact_id": second, "type": "lab", "spec_id": SPEC_ID, "status": "running"},
    ]

    with f.tx() as tx:
        assert f.service.list_artifacts(tx, user_id=USER_ID, ws_id=WS_ID, spec_id="nope") == []
        assert f.service.list_artifacts(tx, user_id="usr_other", ws_id=WS_ID) == []
        assert f.service.list_artifacts(tx, user_id=USER_ID, ws_id="ws_other") == []


def test_check_runs_only_allowed_checks_in_the_lab() -> None:
    f = build()
    artifact_id = start(f)
    params: dict[str, Any] = {"argv": ["true"], "expected_exit_code": 0}
    with f.tx() as tx:
        result = f.service.check(
            tx,
            SPECS,
            artifact_id,
            "fake.exit",
            params,
            event_id=new_key(),
            user_id=USER_ID,
            ws_id=WS_ID,
        )
    assert result["passed"] is True and result["check_id"] == "fake.exit"
    assert f.labs.exec_calls[-1][1].argv == ("true",)
    assert f.types()[-1] == "artifact.checked"

    for check_id, bad in (("fake.other", {}), ("fake.exit", {"argv": "true"})):
        with pytest.raises(ArtifactError) as err, f.tx() as tx:
            f.service.check(
                tx,
                SPECS,
                artifact_id,
                check_id,
                bad,
                event_id=new_key(),
                user_id=USER_ID,
                ws_id=WS_ID,
            )
        assert err.value.status == 422


def test_failed_check_is_an_observation() -> None:
    f = build()

    def fail(lab_id: str, request: ExecRequest) -> ExecResult:
        return ExecResult(
            exit_code=1,
            stdout=b"",
            stderr=b"",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    f.labs._exec_handler = fail  # noqa: SLF001
    artifact_id = start(f)
    params: dict[str, Any] = {"argv": ["false"], "expected_exit_code": 0}
    with f.tx() as tx:
        checked = f.service.check(
            tx,
            SPECS,
            artifact_id,
            "fake.exit",
            params,
            event_id=new_key(),
            user_id=USER_ID,
            ws_id=WS_ID,
        )
    assert checked["passed"] is False


def test_idle_ws_stops_its_lab() -> None:
    f = build()
    artifact_id = start(f)
    f.clock.advance(30)
    assert f.service.reap_idle(SPECS) == []
    f.clock.advance(61)
    assert f.service.reap_idle(SPECS) == [artifact_id]
    stopped = f.store.read(ws_id=WS_ID)[-1]
    assert stopped.type == "artifact.stopped" and stopped.actor == "system"
    assert stopped.payload["reason"] == "idle" and f.labs.labs == {}


def test_idle_stop_is_idempotent_across_workers() -> None:
    """Two API workers reaping the same lab: the second finds it stopped and skips it."""
    f = build()
    artifact_id = start(f)
    f.clock.advance(120)
    assert f.service.reap_idle(SPECS) == [artifact_id]
    assert f.service.reap_idle(SPECS) == []
    # The stop the other worker records is the same event (same derived id), so a
    # replayed pass appends nothing and never raises.
    stops = [e for e in f.store.read(ws_id=WS_ID) if e.type == "artifact.stopped"]
    assert len(stops) == 1


def test_spec_without_idle_limit_only_stops_on_request() -> None:
    f = build()
    start(f)
    body = {k: v for k, v in SPEC["spec"].items() if k != "idle_seconds"}
    f.clock.advance(10_000)
    assert f.service.reap_idle({SPEC_ID: {**SPEC, "spec": body}}) == []
