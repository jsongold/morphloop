"""RFC 8785 JSON Canonicalization Scheme (JCS) and the pack hashes built on it.

Two hashes are defined in ``contracts/schemas/pack/README.md``:

- **Document hash**: ``sha256:`` + hex SHA-256 of the JCS form of a parsed pack
  document. It does not depend on YAML/JSON formatting.
- **Pack content hash** (ADR-0010): ``sha256:`` + hex SHA-256 of the JCS form of
  the list of ``{"path", "content_hash"}`` for every file of the pack (manifest
  and ``eval/`` included), sorted by path (code point order), where each file's
  ``content_hash`` is ``sha256:`` + hex SHA-256 of its raw bytes (the
  ``common/content-hash.json`` format).

JCS summary: object members sorted by the UTF-16 code units of their names, no
insignificant whitespace, strings escaped as ECMAScript ``JSON.stringify`` does
(only ``"``, ``\\`` and control characters), numbers formatted as ECMAScript
``Number.prototype.toString`` (shortest round-trip digits), UTF-8 output.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence

from harness.core.ports import JsonValue

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _string(value: str) -> str:
    out = ['"']
    for ch in value:
        escaped = _ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _float(value: float) -> str:
    """ECMAScript Number::toString for a finite double."""
    if not math.isfinite(value):
        raise ValueError(f"JCS cannot represent {value!r}")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))  # shortest round-trip digits
    mantissa, _, exp_text = text.partition("e")
    exponent = int(exp_text) if exp_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    all_digits = int_part + frac_part
    point = len(int_part) + exponent  # decimal point after this many digits of all_digits
    stripped = all_digits.lstrip("0")
    point -= len(all_digits) - len(stripped)
    digits = stripped.rstrip("0")
    k, n = len(digits), point
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        exp_part = f"e{'+' if e >= 0 else '-'}{abs(e)}"
        body = (digits if k == 1 else digits[0] + "." + digits[1:]) + exp_part
    return sign + body


def _number(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) <= 2**53:
            return str(value)
        return _float(float(value))
    return _float(value)


def _encode(value: JsonValue, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int | float):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be str, got {type(key).__name__}")
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    else:
        raise TypeError(f"not a JSON value: {type(value).__name__}")


def canonicalize(value: JsonValue) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``value``."""
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def sha256_hash(data: bytes) -> str:
    """``sha256:<64 lowercase hex>`` of ``data`` (``common/content-hash.json``)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def document_hash(document: JsonValue) -> str:
    """Document hash of a parsed pack document (see module docstring)."""
    return sha256_hash(canonicalize(document))


def pack_content_hash(files: Iterable[tuple[str, bytes]]) -> str:
    """Pack content hash over ``(path, raw bytes)`` of every pack file.

    Raises ``ValueError`` on a duplicate path.
    """
    entries: dict[str, str] = {}
    for path, data in files:
        if path in entries:
            raise ValueError(f"duplicate pack path {path!r}")
        entries[path] = sha256_hash(data)
    listing: list[JsonValue] = [
        {"path": path, "content_hash": entries[path]} for path in sorted(entries)
    ]
    return document_hash(listing)
