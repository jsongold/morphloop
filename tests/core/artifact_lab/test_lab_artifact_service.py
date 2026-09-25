"""LabArtifact lifecycle over the Ports (#62): start, reset, stop, check, idle stop."""

from __future__ import annotations

from pathlib import Path

import pytest
from artifact_lab.lab_fixture import (
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

from harness.core.artifact import artifact_class
from harness.core.artifact_lab import ArtifactError, ArtifactView, LabArtifact
from harness.core.ports.lab_runtime import ExecRequest, ExecResult


def _types(f: LabFixture) -> list[str]:
    return [e.type for e in f.store.read(ws_id=WS_ID)]


def _status(call: object) -> int:
    with pytest.raises(ArtifactError) as err:
        call()  # type: ignore[operator]
    return err.value.status


def test_lab_is_a_registered_artifact_needing_a_terminal() -> None:
    assert artifact_class("lab") is LabArtifact
    assert LabArtifact.capabilities == frozenset({"terminal"})
    artifact = LabArtifact.from_spec("art_1", SPEC)
    assert artifact.allowed_checks == frozenset({"fake.exit"}) and artifact.idle_seconds == 60


def test_spec_whose_fixture_is_not_allowed_is_refused() -> None:
    bad = {**SPEC, "spec": {**SPEC["spec"], "allowed_fixtures": ["fake.other"]}}  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="allowed_fixtures"):
        LabArtifact.from_spec("art_1", bad)


def test_start_reset_stop(tmp_path: Path) -> None:
    f = build(tmp_path)
    artifact_id = start(f)
    started = f.service.get(artifact_id, user_id=USER_ID, ws_id=WS_ID)
    first = started["lab"]["lab_instance_id"]  # type: ignore[index]
    assert started["status"] == "running" and first in f.labs.labs
    assert started["session_id"] == "ses_1"

    reset = f.service.reset(SPECS, artifact_id, event_id=new_key(), ws_id=WS_ID)
    second = reset["lab"]["lab_instance_id"]  # type: ignore[index]
    assert second != first and list(f.labs.labs) == [second]

    stopped = f.service.stop(artifact_id, event_id=new_key(), ws_id=WS_ID)
    assert stopped["status"] == "stopped" and f.labs.labs == {}
    assert _types(f)[1:] == ["artifact.started", "artifact.reset", "artifact.stopped"]
    assert _status(lambda: f.service.reset(SPECS, artifact_id, event_id=new_key())) == 409


def test_resent_event_id_does_not_act_twice(tmp_path: Path) -> None:
    f = build(tmp_path)
    key = new_key()
    first = f.service.start(SPECS, user_id=USER_ID, ws_id=WS_ID, spec_id=SPEC_ID, event_id=key)
    again = f.service.start(SPECS, user_id=USER_ID, ws_id=WS_ID, spec_id=SPEC_ID, event_id=key)
    assert again == first and len(f.labs.labs) == 1
    artifact_id = str(first["artifact_id"])
    stop_key = new_key()
    f.service.stop(artifact_id, event_id=stop_key, ws_id=WS_ID)
    assert f.service.stop(artifact_id, event_id=stop_key, ws_id=WS_ID)["status"] == "stopped"
    assert _status(lambda: f.service.stop(artifact_id, event_id=key, ws_id=WS_ID)) == 409


def test_view_rebuilds_from_events(tmp_path: Path) -> None:
    f = build(tmp_path)
    artifact_id = start(f)
    f.service.stop(artifact_id, event_id=new_key())
    with f.store.transaction() as tx:
        before = ArtifactView.get(tx, artifact_id)
        ArtifactView.rebuild(f.store.read(), tx)
        assert ArtifactView.get(tx, artifact_id) == before


def test_unknown_ws_spec_artifact_and_user(tmp_path: Path) -> None:
    f = build(tmp_path)

    def start_in(user_id: str, ws_id: str, spec_id: str) -> object:
        return f.service.start(
            SPECS, user_id=user_id, ws_id=ws_id, spec_id=spec_id, event_id=new_key()
        )

    assert _status(lambda: start_in(USER_ID, "ws_nope", SPEC_ID)) == 404
    assert _status(lambda: start_in("usr_other", WS_ID, SPEC_ID)) == 404
    assert _status(lambda: start_in(USER_ID, WS_ID, "nope")) == 404
    assert _status(lambda: f.service.get("art_nope")) == 404
    artifact_id = start(f)
    assert _status(lambda: f.service.get(artifact_id, ws_id="ws_other")) == 404
    assert _status(lambda: f.service.get(artifact_id, user_id="usr_other")) == 404


def test_check_runs_only_allowed_checks_in_the_lab(tmp_path: Path) -> None:
    f = build(tmp_path)
    artifact_id = start(f)
    params = {"argv": ["true"], "expected_exit_code": 0}
    result = f.service.check(SPECS, artifact_id, "fake.exit", params, event_id=new_key())
    assert result["passed"] is True and result["check_id"] == "fake.exit"
    assert f.labs.exec_calls[-1][1].argv == ("true",)
    assert _types(f)[-1] == "artifact.checked"

    for check_id, bad in (("fake.other", {}), ("fake.exit", {"argv": "true"})):
        with pytest.raises(ArtifactError) as err:
            f.service.check(SPECS, artifact_id, check_id, bad, event_id=new_key())
        assert err.value.status == 422


def test_failed_check_is_an_observation(tmp_path: Path) -> None:
    f = build(tmp_path)

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
    params = {"argv": ["false"], "expected_exit_code": 0}
    assert (
        f.service.check(SPECS, artifact_id, "fake.exit", params, event_id=new_key())["passed"]
        is False
    )


def test_idle_ws_stops_its_lab(tmp_path: Path) -> None:
    f = build(tmp_path)
    artifact_id = start(f)
    f.clock.advance(30)
    assert f.service.reap_idle(SPECS) == []
    f.clock.advance(61)
    assert f.service.reap_idle(SPECS) == [artifact_id]
    stopped = f.store.read(ws_id=WS_ID)[-1]
    assert stopped.type == "artifact.stopped" and stopped.actor == "system"
    assert stopped.payload["reason"] == "idle" and f.labs.labs == {}


def test_spec_without_idle_limit_only_stops_on_request(tmp_path: Path) -> None:
    f = build(tmp_path)
    start(f)
    body = {k: v for k, v in SPEC["spec"].items() if k != "idle_seconds"}  # type: ignore[union-attr]
    f.clock.advance(10_000)
    assert f.service.reap_idle({SPEC_ID: {**SPEC, "spec": body}}) == []
