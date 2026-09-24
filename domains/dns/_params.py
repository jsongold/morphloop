"""Strict readers for pack-declared adapter params (raise ``AdapterParamsError``).

Unknown keys are rejected so a typo in a pack fails at import instead of being
silently ignored.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    create_model,
)

from harness.core.domain_adapter import AdapterParamsError
from harness.core.ports.json_types import JsonObject, JsonValue

# RFC 1123 host name, lower- or upper-case, optional trailing dot, no leading '-'
# (so it can never be mistaken for an option when passed as an argv element).
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_NAME_RE = re.compile(rf"^(?=.{{1,253}}\.?$){_LABEL}(?:\.{_LABEL})*\.?$")
# resolv.conf option token, e.g. "timeout:1", "attempts:2", "rotate", "edns0".
_RESOLV_OPTION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?::[0-9]{1,5})?$")
_HTTP_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,255}$")

_OBJ_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])
_ARRAY_ADAPTER: TypeAdapter[list[Any]] = TypeAdapter(list[Any])
_STRING_ADAPTER: TypeAdapter[str] = TypeAdapter(Annotated[StrictStr, Field(min_length=1)])


@lru_cache(maxsize=64)
def _int_adapter(minimum: int, maximum: int) -> TypeAdapter[int]:
    return TypeAdapter(Annotated[StrictInt, Field(ge=minimum, le=maximum)])


@lru_cache(maxsize=64)
def _enum_adapter(choices: tuple[str, ...]) -> TypeAdapter[str]:
    return TypeAdapter(Literal[choices])


@lru_cache(maxsize=128)
def _only_keys_model(required: frozenset[str], optional: frozenset[str]) -> type[BaseModel]:
    fields: dict[str, Any] = {k: (Any, ...) for k in required}
    fields.update((k, (Any, None)) for k in optional)
    return create_model("_OnlyKeysParams", __config__=ConfigDict(extra="forbid"), **fields)


def obj(value: JsonValue, where: str) -> JsonObject:
    try:
        return _OBJ_ADAPTER.validate_python(value)
    except ValidationError:
        raise AdapterParamsError(f"{where} must be an object") from None


def only_keys(value: JsonObject, where: str, *, required: set[str], optional: set[str]) -> None:
    model = _only_keys_model(frozenset(required), frozenset(optional))
    try:
        model.model_validate(dict(value))
    except ValidationError as exc:
        errors = exc.errors()
        missing = sorted(str(err["loc"][0]) for err in errors if err["type"] == "missing")
        if missing:
            raise AdapterParamsError(f"{where} is missing {', '.join(missing)}") from None
        unknown = sorted(str(err["loc"][0]) for err in errors if err["type"] == "extra_forbidden")
        raise AdapterParamsError(f"{where} has unknown keys: {', '.join(unknown)}") from None


def array(value: JsonValue, where: str, *, min_items: int) -> Sequence[JsonValue]:
    try:
        items = _ARRAY_ADAPTER.validate_python(value)
    except ValidationError:
        raise AdapterParamsError(f"{where} must be an array") from None
    if len(items) < min_items:
        raise AdapterParamsError(f"{where} must have at least {min_items} item(s)")
    return items


def string(value: JsonValue, where: str) -> str:
    try:
        return _STRING_ADAPTER.validate_python(value)
    except ValidationError:
        raise AdapterParamsError(f"{where} must be a non-empty string") from None


def integer(value: JsonValue, where: str, *, minimum: int, maximum: int) -> int:
    try:
        return _int_adapter(minimum, maximum).validate_python(value)
    except ValidationError as exc:
        if any(err["type"] == "int_type" for err in exc.errors()):
            raise AdapterParamsError(f"{where} must be an integer") from None
        raise AdapterParamsError(f"{where} must be between {minimum} and {maximum}") from None


def seconds(value: JsonValue, where: str, *, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise AdapterParamsError(f"{where} must be a number")
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise AdapterParamsError(f"{where} must be > 0 and <= {maximum}")
    return float(value)


def enum(value: JsonValue, where: str, choices: Sequence[str]) -> str:
    try:
        return _enum_adapter(tuple(choices)).validate_python(value)
    except ValidationError:
        raise AdapterParamsError(f"{where} must be one of {', '.join(choices)}") from None


def dns_name(value: JsonValue, where: str) -> str:
    """A host name, returned lower-cased without a trailing dot."""
    text = string(value, where)
    if not _DNS_NAME_RE.fullmatch(text):
        raise AdapterParamsError(f"{where} must be a DNS host name, got {text!r}")
    return text.lower().rstrip(".")


def ipv4(value: JsonValue, where: str) -> str:
    text = string(value, where)
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise AdapterParamsError(f"{where} must be an IPv4 address, got {text!r}") from None


def ip(value: JsonValue, where: str) -> str:
    text = string(value, where)
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        raise AdapterParamsError(f"{where} must be an IP address, got {text!r}") from None


def resolv_option(value: JsonValue, where: str) -> str:
    text = string(value, where)
    if not _RESOLV_OPTION_RE.fullmatch(text):
        raise AdapterParamsError(f"{where} must be a resolv.conf option, got {text!r}")
    return text


def http_path(value: JsonValue, where: str) -> str:
    text = string(value, where)
    if not _HTTP_PATH_RE.fullmatch(text):
        raise AdapterParamsError(f"{where} must be an absolute URL path, got {text!r}")
    return text


def http_url(value: JsonValue, where: str) -> str:
    """An absolute ``http``/``https`` URL with a host name or IP and no credentials."""
    text = string(value, where)
    error = AdapterParamsError(f"{where} must be an http(s) URL, got {text!r}")
    if len(text) > 2048 or any(ch.isspace() or not ch.isprintable() for ch in text):
        raise error
    try:
        parts = urlsplit(text)
        port_ok = parts.port is None or parts.port > 0  # .port raises on a bad port
    except ValueError:
        raise error from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
        raise error
    if not port_ok:
        raise error
    return text


def argv(value: JsonValue, where: str) -> tuple[str, ...]:
    items = array(value, where, min_items=1)
    out: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str) or "\x00" in item:
            raise AdapterParamsError(f"{where}[{i}] must be a string without NUL")
        out.append(item)
    if not out[0] or any(ch.isspace() for ch in out[0]):
        raise AdapterParamsError(f"{where}[0] must be non-empty without whitespace")
    return tuple(out)
