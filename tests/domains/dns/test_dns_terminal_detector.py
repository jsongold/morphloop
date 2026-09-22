"""Unit tests for the dns.terminal shell-integration command detector."""

from __future__ import annotations

import pytest

from domains.dns.terminal import LAUNCH, MARKER_PREFIX, BashTerminalTool, ShellIntegrationDetector
from harness.core.domain_adapter import CommandDetector, DetectedCommand


def _hex(text: str) -> str:
    return text.encode().hex()


def prompt(n: int, terminator: bytes = b"\x07") -> bytes:
    return MARKER_PREFIX + f"prompt;{n}".encode() + terminator


def cmd(n: int, cwd: str, command: str, terminator: bytes = b"\x07") -> bytes:
    return MARKER_PREFIX + f"cmd;{n};{_hex(cwd)};{_hex(command)}".encode() + terminator


# A realistic session: prompt, echo of typed input, marker, command output.
SESSION = b"".join(
    [
        prompt(0),
        b"root@lab:~# cd /tmp\r\n",
        cmd(1, "/root", "cd /tmp"),
        prompt(1),
        b"root@lab:/tmp# dig api.corp.internal\r\n",
        cmd(2, "/tmp", "dig api.corp.internal"),
        b";; communications error to 192.0.2.53#53: timed out\r\n\x1b[1mbold\x1b[0m\r\n",
        prompt(2),
        b"root@lab:/tmp# \r\n",  # empty line: no PS0 marker
        prompt(2),
        "root@lab:/tmp# echo 日本語\r\n".encode(),
        cmd(3, "/tmp", "echo 日本語", terminator=b"\x1b\\"),
        "日本語\r\n".encode(),
        prompt(3),
    ]
)
EXPECTED = [
    DetectedCommand(command="cd /tmp", cwd="/root"),
    DetectedCommand(command="dig api.corp.internal", cwd="/tmp"),
    DetectedCommand(command="echo 日本語", cwd="/tmp"),
]


def _feed(chunks: list[bytes]) -> list[DetectedCommand]:
    detector = ShellIntegrationDetector()
    out: list[DetectedCommand] = []
    for chunk in chunks:
        out.extend(detector.feed_output(chunk))
    return out


def test_whole_stream() -> None:
    assert _feed([SESSION]) == EXPECTED


def test_every_split_point() -> None:
    for i in range(len(SESSION) + 1):
        assert _feed([SESSION[:i], SESSION[i:]]) == EXPECTED, f"split at {i}"


def test_byte_by_byte() -> None:
    assert _feed([SESSION[i : i + 1] for i in range(len(SESSION))]) == EXPECTED


@pytest.mark.parametrize("size", [2, 3, 5, 7, 11])
def test_fixed_size_chunks(size: int) -> None:
    chunks = [SESSION[i : i + size] for i in range(0, len(SESSION), size)]
    assert _feed(chunks) == EXPECTED


def test_commands_are_returned_by_the_chunk_that_completes_them() -> None:
    detector = ShellIntegrationDetector()
    marker = cmd(1, "/", "ls")
    assert detector.feed_output(marker[:-1]) == []
    assert detector.feed_output(marker[-1:]) == [DetectedCommand(command="ls", cwd="/")]


def test_repeated_history_number_is_not_a_new_command() -> None:
    # e.g. the learner turned HISTCONTROL=ignorespace back on: PS0 re-reports
    # the previous history entry, which must not be counted twice.
    assert _feed([prompt(4) + cmd(4, "/", "ls")]) == []
    twice = cmd(5, "/", "ls") + cmd(5, "/", "ls")
    assert _feed([twice]) == [DetectedCommand(command="ls", cwd="/")]


def test_history_restart_after_clear() -> None:
    stream = cmd(7, "/", "history -c") + prompt(0) + cmd(1, "/", "pwd")
    assert _feed([stream]) == [
        DetectedCommand(command="history -c", cwd="/"),
        DetectedCommand(command="pwd", cwd="/"),
    ]


def test_input_is_ignored() -> None:
    detector = ShellIntegrationDetector()
    assert detector.feed_input(b"ls -la\r") == ()
    assert detector.feed_input(cmd(1, "/", "ls")) == ()


@pytest.mark.parametrize(
    "garbage",
    [
        MARKER_PREFIX + b"cmd;1;zz;6c73\x07",  # bad hex
        MARKER_PREFIX + b"cmd;1;2f;6c7\x07",  # odd hex length
        MARKER_PREFIX + b"cmd;x;2f;6c73\x07",  # bad number
        MARKER_PREFIX + b"cmd;1;2f\x07",  # missing field
        MARKER_PREFIX + b"other;1\x07",  # unknown kind
        MARKER_PREFIX + b"cmd;1;2f;6c73\x1bX",  # ESC not followed by '\'
        MARKER_PREFIX + b"cmd;1;2f;6C73\x07",  # upper-case: not our encoding
        MARKER_PREFIX + b"cmd;1;2f;2020\x07",  # whitespace-only command
    ],
)
def test_malformed_markers_are_ignored_and_parsing_recovers(garbage: bytes) -> None:
    assert _feed([garbage + cmd(9, "/", "ls")]) == [DetectedCommand(command="ls", cwd="/")]


def test_empty_cwd_becomes_none() -> None:
    assert _feed([cmd(1, "", "ls")]) == [DetectedCommand(command="ls", cwd=None)]


def test_command_is_stripped_but_keeps_inner_text() -> None:
    assert _feed([cmd(1, "/", "  echo 'a;b'  ")]) == [
        DetectedCommand(command="echo 'a;b'", cwd="/")
    ]


def test_oversized_marker_is_dropped_and_buffer_stays_bounded() -> None:
    detector = ShellIntegrationDetector()
    detector.feed_output(MARKER_PREFIX + b"cmd;1;" + b"a" * (300 * 1024))
    assert len(detector._buf) < 300 * 1024
    assert list(detector.feed_output(cmd(2, "/", "ls"))) == [DetectedCommand(command="ls", cwd="/")]


def test_plain_output_is_not_buffered() -> None:
    detector = ShellIntegrationDetector()
    detector.feed_output(b"x" * 10000 + MARKER_PREFIX[:3])
    assert bytes(detector._buf) == MARKER_PREFIX[:3]


def test_tool_launch_and_fresh_detectors() -> None:
    tool = BashTerminalTool()
    launch = tool.launch()
    assert launch is LAUNCH
    assert launch.argv == ("/bin/bash", "--noprofile", "--norc", "-i")
    assert "PROMPT_COMMAND" in launch.env and launch.env["HISTCONTROL"] == ""
    first: CommandDetector = tool.new_command_detector()
    second = tool.new_command_detector()
    assert first is not second
