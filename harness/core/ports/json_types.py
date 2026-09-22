"""JSON value types and helpers shared by the Ports.

Payloads, projection documents and LLM outputs cross the Ports as JSON values.
They are typed as read-only ``Mapping`` / ``Sequence`` so that a plain ``dict``
literal of any JSON-compatible value type is accepted, and are turned back into
plain ``dict`` / ``list`` trees with :func:`to_plain_json` when they must be
serialized or validated against a ``contracts/`` schema.

Timestamps are ``datetime`` values inside the harness and RFC 3339 UTC strings
with a ``Z`` suffix on the wire (``contracts/schemas/common/timestamp.json``);
:func:`format_timestamp` does the conversion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Sequence[JsonValue] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]
type PlainJson = JsonScalar | list[PlainJson] | dict[str, PlainJson]


def to_plain_json(value: JsonValue) -> PlainJson:
    """Deep-copy ``value`` into plain ``dict`` / ``list`` / scalar values.

    ``str`` is a ``Sequence`` too, so scalars are checked first. Raises
    ``TypeError`` for anything that is not a JSON value (e.g. bytes, datetime).
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        out: dict[str, PlainJson] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be str, got {type(key).__name__}")
            out[key] = to_plain_json(item)
        return out
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [to_plain_json(item) for item in value]
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def to_plain_object(value: JsonObject) -> dict[str, PlainJson]:
    """Like :func:`to_plain_json` for a JSON object, keeping the ``dict`` type."""
    plain = to_plain_json(value)
    assert isinstance(plain, dict)
    return plain


def require_aware(name: str, value: datetime) -> None:
    """Raise ``ValueError`` unless ``value`` carries a timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got a naive datetime")


def format_timestamp(value: datetime) -> str:
    """Format an aware ``datetime`` as RFC 3339 UTC with a ``Z`` suffix.

    Whole seconds are written without a fraction; otherwise microseconds are
    kept. Both match ``contracts/schemas/common/timestamp.json``.
    """
    require_aware("timestamp", value)
    utc = value.astimezone(UTC)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.replace(tzinfo=None).isoformat(timespec=timespec) + "Z"
