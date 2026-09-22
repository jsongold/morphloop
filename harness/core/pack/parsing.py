"""Parse raw pack file bytes into plain JSON values.

A pack document may be JSON (``.json``) or YAML (``.yaml`` / ``.yml``); the
schemas apply to the parsed value (``contracts/schemas/pack/README.md``). The
result must be a JSON value, so YAML is read with a safe loader that keeps
unquoted timestamps as strings (``generated_at`` is an RFC 3339 string in the
schemas), and anything that is not JSON (binary, sets, non-string keys, NaN,
infinities) is rejected. JSON duplicate keys are rejected too, so the parsed
document, and therefore its hash, is never ambiguous.
"""

from __future__ import annotations

import json
import math
from typing import Any

import yaml

from harness.core.ports import PlainJson

_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class PackParseError(ValueError):
    """A pack file cannot be parsed into a JSON value."""


class _JsonCompatibleLoader(yaml.SafeLoader):
    """``SafeLoader`` without the implicit timestamp resolver."""


_JsonCompatibleLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _TIMESTAMP_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise PackParseError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise PackParseError(f"{name} is not a JSON value")


def _check(value: Any, where: str) -> PlainJson:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PackParseError(f"{where}: {value!r} is not a JSON number")
        return value
    if isinstance(value, dict):
        out: dict[str, PlainJson] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PackParseError(f"{where}: object key {key!r} is not a string")
            out[key] = _check(item, f"{where}.{key}")
        return out
    if isinstance(value, list):
        return [_check(item, f"{where}[{i}]") for i, item in enumerate(value)]
    raise PackParseError(f"{where}: {type(value).__name__} is not a JSON value")


def is_document_path(path: str) -> bool:
    """Whether ``path`` has an extension this module parses."""
    return path.endswith((".json", ".yaml", ".yml"))


def parse_document(path: str, data: bytes) -> PlainJson:
    """Parse ``data`` by the extension of ``path``. Raises :class:`PackParseError`."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackParseError(f"{path}: not UTF-8: {exc}") from exc
    try:
        if path.endswith(".json"):
            value = json.loads(
                text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
            )
        elif path.endswith((".yaml", ".yml")):
            value = yaml.load(text, Loader=_JsonCompatibleLoader)  # noqa: S506 - safe loader
        else:
            raise PackParseError(f"{path}: unsupported extension (expected .json/.yaml/.yml)")
    except PackParseError as exc:
        raise PackParseError(f"{path}: {exc}") from exc
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise PackParseError(f"{path}: {exc}") from exc
    return _check(value, "$")


def parse_text(path: str, data: bytes) -> str:
    """Decode a plain-text pack file (prompts). Raises :class:`PackParseError`."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackParseError(f"{path}: not UTF-8: {exc}") from exc
