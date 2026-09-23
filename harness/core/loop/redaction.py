"""The redaction hook every payload passes before persistence (ADR-0008, ADR-0016).

Events cannot be deleted, so every payload goes through a hook before it is
appended. v0.1 ships the hook only, with no redaction logic
(:class:`NullRedaction`); a deployment can register its own.

Independently of the hook, core rejects a payload holding a NUL code point
(``\\u0000``) in any string: PostgreSQL ``jsonb`` cannot store it, so such an
event could never be appended and must be refused at the boundary instead of
failing deep inside the adapter. Producers that can legitimately see NUL bytes
(terminal output) encode the chunk as base64 rather than passing it here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from harness.core.ports import JsonObject, JsonValue

NUL = "\x00"


class EventRejectedError(ValueError):
    """The payload may not be persisted as it stands."""


class RedactionHook(Protocol):
    """Last chance to remove secrets from a payload before it is stored."""

    def redact(self, event_type: str, payload: JsonObject) -> JsonObject:
        """Return the payload to store. Raise :class:`EventRejectedError` to
        refuse the event entirely."""
        ...


class NullRedaction:
    """The v0.1 hook: keeps every payload unchanged (ADR-0016)."""

    def redact(self, event_type: str, payload: JsonObject) -> JsonObject:
        return payload


def reject_nul(where: str, value: JsonValue) -> None:
    """Raise :class:`EventRejectedError` if any string under ``value`` holds NUL."""
    if isinstance(value, str):
        if NUL in value:
            raise EventRejectedError(f"{where}: strings must not contain NUL (\\u0000)")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            reject_nul(f"{where}.{key}", key)
            reject_nul(f"{where}.{key}", item)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for index, item in enumerate(value):
            reject_nul(f"{where}[{index}]", item)


def strip_nul(text: str) -> str:
    """Drop NUL code points from a string a producer assembled itself."""
    return text.replace(NUL, "")
