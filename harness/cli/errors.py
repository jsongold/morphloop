"""The one error type the CLI turns into a message and a non-zero exit code."""

from __future__ import annotations


class CommandError(Exception):
    """A command cannot do what was asked. Reported as ``error: <message>``.

    Raised for every expected failure (a missing pack, a Template that is not
    in the pack, a candidate that never passed validation). Anything else
    propagates as a traceback, because it is a bug.
    """
