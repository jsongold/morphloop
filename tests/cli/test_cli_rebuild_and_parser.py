"""Tests for ``harness.cli.rebuild`` and the typer CLI surface of ``harness.cli.main``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli.main import app, main
from harness.cli.rebuild import format_result, rebuild
from harness.core.loop import LOOP_PROJECTIONS
from harness.testing.fakes import InMemoryEventStore

runner = CliRunner()


def test_rebuilding_an_empty_log_replays_nothing_and_clears_the_projections() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        tx.put_projection(LOOP_PROJECTIONS[0], "stale", {"left": "over"})

    replayed = rebuild(store)

    assert replayed == 0
    with store.transaction() as tx:
        for projection in LOOP_PROJECTIONS:
            assert tx.list_projection(projection) == []
    assert "replayed      0 event(s)" in format_result(replayed)


def test_rebuild_does_not_touch_the_pack_projection() -> None:
    store = InMemoryEventStore()
    with store.transaction() as tx:
        tx.put_projection("pack", "software-engineering/0.1.0/sha256:0", {"kept": True})

    rebuild(store)

    with store.transaction() as tx:
        assert tx.get_projection("pack", "software-engineering/0.1.0/sha256:0") == {"kept": True}


_V2_REBUILD_SCRIPT = """
import json
from harness.cli.rebuild import rebuild
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.events_v2 import EventV2
from harness.core.view import registered_views
from harness.testing.fakes import InMemoryEventStore
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

rebuild(InMemoryEventStore(), store_v2)

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


def _params(argv: list[str]) -> dict[str, object]:
    """Parse ``argv`` through the real typer command and return its ``ctx.params``.

    Uses ``make_context`` (parsing only, no callback invocation) so these tests
    check the CLI surface -- names, options, defaults -- without running the
    command body (which needs a database/pack on disk).
    """
    from typer.main import get_command

    group = get_command(app)
    name = argv[0]
    return group.commands[name].make_context(name, argv[1:]).params


def test_generate_takes_a_pack_and_a_template() -> None:
    params = _params(["generate", "contents/software-engineering", "--template", "diagnose-dns"])

    assert params["pack"] == "contents/software-engineering"
    assert params["template"] == "diagnose-dns"
    assert params["activity_id"] is None
    assert params["max_attempts"] is None


def test_generate_takes_an_explicit_id_and_attempt_count() -> None:
    params = _params(
        [
            "generate",
            "contents/software-engineering",
            "--template",
            "diagnose-dns",
            "--activity-id",
            "gen-dns-001",
            "--max-attempts",
            "2",
        ]
    )

    assert params["activity_id"] == "gen-dns-001"
    assert params["max_attempts"] == 2


def test_generate_requires_a_template() -> None:
    result = runner.invoke(app, ["generate", "contents/software-engineering"])

    assert result.exit_code == 2


def test_import_takes_a_pack_and_rebuild_and_migrate_take_nothing() -> None:
    assert _params(["import", "contents/x"])["pack"] == "contents/x"
    assert _params(["rebuild"]) == {}
    assert _params(["migrate"]) == {}


def test_a_command_is_required() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 2


def test_main_reports_a_failure_as_a_message_and_exit_code_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Reading the pack fails before the event store is ever used, so this needs no database.
    code = main(["import", str(tmp_path / "absent")])

    assert code == 1
    assert capsys.readouterr().err.startswith("error: cannot read the pack")


# CliRunner (used above) catches every exception itself, so it can't tell a usage
# error handled by ``main`` apart from one that escapes it uncaught. These call
# ``main`` directly, and via a real subprocess, to pin exit codes 2/1 and rule out
# a raw traceback reaching the terminal (regression: a wrong exception type in
# ``main``'s except clause let typer's usage errors escape uncaught).


def test_main_treats_a_missing_command_as_a_usage_error_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_main_treats_generates_missing_arguments_as_a_usage_error_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["generate"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "harness.cli", *args],
        capture_output=True,
        text=True,
    )


def test_the_real_entry_point_reports_a_missing_command_as_exit_code_two() -> None:
    result = _run_cli()

    assert result.returncode == 2
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_the_real_entry_point_reports_generates_missing_arguments_as_exit_code_two() -> None:
    result = _run_cli("generate")

    assert result.returncode == 2
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
