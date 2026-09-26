"""Tests for ``harness.cli.rebuild`` and the typer CLI surface of ``harness.cli.main``."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from harness.cli.main import app, main
from harness.cli.rebuild import format_result, rebuild
from harness.core.contract_schemas import ContractSchemas
from harness.testing.fakes_v2 import InMemoryEventStoreV2

runner = CliRunner()


def test_rebuilding_an_empty_log_replays_nothing() -> None:
    replayed = rebuild(InMemoryEventStoreV2(ContractSchemas.load()))

    assert replayed == 0
    assert "replayed      0 event(s)" in format_result(replayed)


_V2_REBUILD_SCRIPT = """
import json
from harness.cli.rebuild import rebuild
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.events_v2 import EventV2
from harness.core.view import registered_views
from harness.testing.fakes_v2 import InMemoryEventStoreV2

store_v2 = InMemoryEventStoreV2(ContractSchemas.load())
common = dict(actor="learner", user_id="usr_01", session_id="ses_01", ws_id="ws_01")
events = [
    ("ws.created", {"labels": ["origin:learner"]}),
    ("thread.created", {"thread_id": "thr_01"}),
    ("chat.sent", {"thread_id": "thr_01", "message_id": "msg_0190f5a27c3e7d4b8a1f000000000003",
                   "text": "hello", "allow_writes": False}),
    ("memo.appended", {"entry_id": "ent_0190f5a27c3e7d4b8a1f000000000004", "body": "remember"}),
]
with store_v2.transaction() as tx:
    for n, (type_, payload) in enumerate(events, start=1):
        event_id = f"0190f5a2-7c3e-7d4b-8a1f-{n:012d}"
        tx.append(EventV2(id=event_id, type=type_, payload=payload, **common))
    assert registered_views() == {}, "views registered before rebuild; the test would prove nothing"

rebuild(store_v2)

with store_v2.transaction() as tx:
    print(json.dumps({name: view.list(tx) for name, view in registered_views().items()}))
"""


def test_rebuild_registers_and_rebuilds_v2_views_from_the_log() -> None:
    # A fresh interpreter: the view registry is process-global, so other test
    # modules importing views would otherwise make this pass vacuously.
    # Events are appended without dispatch, so every document comes from rebuild.
    done = subprocess.run(
        [sys.executable, "-c", _V2_REBUILD_SCRIPT], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    views = json.loads(done.stdout)
    for name in ("ws", "chat.thread", "chat.messages", "memo_entries"):
        assert views.get(name), f"{name} not rebuilt: {views}"


def test_rebuild_is_a_named_command_taking_nothing() -> None:
    result = runner.invoke(app, ["rebuild", "--help"])

    assert result.exit_code == 0
    assert "Rebuild every registered v0.2 view" in result.output


def test_migrate_is_a_named_command_taking_nothing() -> None:
    result = runner.invoke(app, ["migrate", "--help"])

    assert result.exit_code == 0
    assert "alembic migrations" in result.output


def test_a_command_is_required() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 2


def test_main_reports_a_failure_as_a_message_and_exit_code_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MORPHLOOP_CONTRACTS_DIR", "/nonexistent-contracts")

    code = main(["rebuild"])

    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")


# CliRunner (used above) catches every exception itself, so it can't tell a usage
# error handled by ``main`` apart from one that escapes it uncaught. These call
# ``main`` directly, and via a real subprocess, to pin exit code 2 and rule out
# a raw traceback reaching the terminal (regression: a wrong exception type in
# ``main``'s except clause let typer's usage errors escape uncaught).


def test_main_treats_a_missing_command_as_a_usage_error_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_main_treats_an_unknown_command_as_a_usage_error_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["import"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_the_real_entry_point_reports_a_missing_command_as_exit_code_two() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "harness.cli"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
