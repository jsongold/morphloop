"""Tests for ``harness.cli.rebuild`` and the argparse surface of ``harness.cli.main``."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.cli.main import build_parser, main
from harness.cli.rebuild import format_result, rebuild
from harness.core.loop import LOOP_PROJECTIONS
from harness.testing.fakes import InMemoryEventStore


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


def test_generate_takes_a_pack_and_a_template() -> None:
    args = build_parser().parse_args(
        ["generate", "contents/software-engineering", "--template", "diagnose-dns"]
    )

    assert args.command == "generate"
    assert args.pack == "contents/software-engineering"
    assert args.template == "diagnose-dns"
    assert args.activity_id is None
    assert args.max_attempts is None


def test_generate_takes_an_explicit_id_and_attempt_count() -> None:
    args = build_parser().parse_args(
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

    assert args.activity_id == "gen-dns-001"
    assert args.max_attempts == 2


def test_generate_requires_a_template() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["generate", "contents/software-engineering"])

    assert excinfo.value.code == 2


def test_import_takes_a_pack_and_rebuild_takes_nothing() -> None:
    parser = build_parser()

    assert parser.parse_args(["import", "contents/x"]).pack == "contents/x"
    assert parser.parse_args(["rebuild"]).command == "rebuild"


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([])

    assert excinfo.value.code == 2


def test_main_reports_a_failure_as_a_message_and_exit_code_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Reading the pack fails before the event store is ever used, so this needs no database.
    code = main(["import", str(tmp_path / "absent")])

    assert code == 1
    assert capsys.readouterr().err.startswith("error: cannot read the pack")
