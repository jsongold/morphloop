"""Terminal tool ``dns.terminal``: a bash shell in the lab, with shell integration.

Launch. ``bash --noprofile --norc -i`` in the image's default working
directory, so the image's rc files cannot change what is detected. All
shell-integration state is passed in the environment (bash imports it as
shell variables): ``PROMPT_COMMAND`` (:data:`SHELL_INTEGRATION`), ``PS1``,
``HISTCONTROL=`` (every line is recorded, including ones starting with a
space), ``HISTFILE=`` (no history is loaded from or saved to disk, so a new
terminal starts empty) and ``TERM``.

Command detection (shell integration, like VS Code / iTerm2 / FinalTerm
``OSC 133``). Reconstructing a command from the learner's keystrokes is not
robust (line editing, history recall, tab completion, paste), so bash itself
reports each command in its output stream, as a private OSC sequence:

- before every prompt (``PROMPT_COMMAND``)::

      ESC ] 6973 ; prompt ; <history number> BEL

- after a command line is read, before it runs (``PS0``, bash >= 4.4)::

      ESC ] 6973 ; cmd ; <history number> ; <hex cwd> ; <hex command> BEL

The command text is ``history 1`` (so it is exactly the line bash executes,
after editing and history expansion); cwd is ``$PWD`` when the command
started. Both are hex-encoded (``od``) so no byte of them can end the
sequence. A ``cmd`` whose history number equals the last one seen (an empty
line, or a line the learner excluded from history) is not a new command and
is skipped. ``ST`` (``ESC \\``) is accepted as a terminator too. Terminal
emulators ignore unknown OSC sequences, so the markers are invisible in
xterm.js and the output can be forwarded unchanged.

Trust. Anything in the lab can print a marker, so a detected command is what
the lab's output claims was run; it is learner evidence, not a fact (the
whole terminal is untrusted input, ``docs/ARCHITECTURE.md``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from types import MappingProxyType

from harness.core.domain_adapter import CommandDetector, DetectedCommand, TerminalLaunch

MARKER_PREFIX = b"\x1b]6973;"
"""Start of every shell-integration OSC sequence (6973: private, unassigned)."""
MAX_MARKER_BYTES = 256 * 1024
"""A marker body longer than this is discarded as garbage."""

# Runs inside the lab's bash only. Fixed text: nothing pack-derived.
SHELL_INTEGRATION = r"""__morphloop_hex() {
  builtin printf %s "$1" | od -An -tx1 -v | tr -dc 0-9a-f
}
__morphloop_hist() {
  local h
  h=$(HISTTIMEFORMAT= builtin history 1)
  [[ $h =~ ^[[:space:]]*([0-9]+)[*[:space:]][[:space:]](.*)$ ]] || return 1
  __morphloop_n=${BASH_REMATCH[1]}
  __morphloop_c=${BASH_REMATCH[2]}
}
__morphloop_ps0() {
  __morphloop_hist || return 0
  builtin printf '\033]6973;cmd;%s;%s;%s\007' "$__morphloop_n" \
    "$(__morphloop_hex "$PWD")" "$(__morphloop_hex "$__morphloop_c")"
}
__morphloop_n=0
__morphloop_hist
builtin printf '\033]6973;prompt;%s\007' "$__morphloop_n"
PS0='$(__morphloop_ps0)'"""

LAUNCH = TerminalLaunch(
    argv=("/bin/bash", "--noprofile", "--norc", "-i"),
    env=MappingProxyType(
        {
            "PROMPT_COMMAND": SHELL_INTEGRATION,
            "PS1": r"\u@\h:\w\$ ",
            "HISTCONTROL": "",
            "HISTFILE": "",
            "TERM": "xterm-256color",
        }
    ),
    workdir=None,
)

_BODY_RE = re.compile(rb"[0-9a-z;]*")
_NUMBER_RE = re.compile(rb"[0-9]{1,18}")
_HEX_RE = re.compile(rb"(?:[0-9a-f]{2})*")


def _partial_prefix_len(buf: bytes | bytearray) -> int:
    """Length of the longest suffix of ``buf`` that is a proper prefix of the marker."""
    for n in range(min(len(buf), len(MARKER_PREFIX) - 1), 0, -1):
        if buf.endswith(MARKER_PREFIX[:n]):
            return n
    return 0


def _unhex(field: bytes) -> str | None:
    if not _HEX_RE.fullmatch(field):
        return None
    return bytes.fromhex(field.decode("ascii")).decode("utf-8", errors="replace")


class ShellIntegrationDetector:
    """:class:`~harness.core.domain_adapter.CommandDetector` for :data:`LAUNCH`.

    Reads only the output stream; input is ignored (bash reports commands).
    Chunks may split anywhere; at most one partial marker is buffered.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._last_number: int | None = None

    def feed_input(self, data: bytes) -> Sequence[DetectedCommand]:
        return ()

    def feed_output(self, data: bytes) -> Sequence[DetectedCommand]:
        self._buf += data
        commands: list[DetectedCommand] = []
        while True:
            start = self._buf.find(MARKER_PREFIX)
            if start < 0:
                keep = _partial_prefix_len(self._buf)
                del self._buf[: len(self._buf) - keep]
                return commands
            del self._buf[:start]
            body_start = len(MARKER_PREFIX)
            match = _BODY_RE.match(self._buf, body_start)
            assert match is not None  # the pattern matches the empty string
            end = match.end()
            if end - body_start > MAX_MARKER_BYTES:
                del self._buf[:body_start]  # garbage: drop the prefix, rescan
                continue
            if end == len(self._buf):
                return commands  # body may continue in the next chunk
            terminator = self._buf[end]
            if terminator == 0x07:
                size = 1
            elif terminator == 0x1B:
                if end + 1 == len(self._buf):
                    return commands  # maybe ESC \ split across chunks
                if self._buf[end + 1] != 0x5C:
                    del self._buf[:body_start]
                    continue
                size = 2
            else:
                del self._buf[:body_start]
                continue
            body = bytes(self._buf[body_start:end])
            del self._buf[: end + size]
            command = self._handle(body)
            if command is not None:
                commands.append(command)

    def _handle(self, body: bytes) -> DetectedCommand | None:
        fields = body.split(b";")
        if not fields or not _NUMBER_RE.fullmatch(fields[1] if len(fields) > 1 else b""):
            return None
        number = int(fields[1])
        if fields[0] == b"prompt" and len(fields) == 2:
            self._last_number = number
            return None
        if fields[0] != b"cmd" or len(fields) != 4:
            return None
        cwd, text = _unhex(fields[2]), _unhex(fields[3])
        if cwd is None or text is None or number == self._last_number:
            return None
        self._last_number = number
        command = text.strip()
        if not command:
            return None
        return DetectedCommand(command=command, cwd=cwd or None)


class BashTerminalTool:
    """``dns.terminal``: see the module docstring."""

    def launch(self) -> TerminalLaunch:
        return LAUNCH

    def new_command_detector(self) -> CommandDetector:
        return ShellIntegrationDetector()
