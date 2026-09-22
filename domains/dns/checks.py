"""Deterministic DNS checks (ADR-0009, ADR-0013).

Every check observes the lab only through ``LabRuntime.exec`` with a fixed
argv (pack-derived values are separate argv elements, validated first; no
shell is involved). A failed or timed-out command is an observation
(``passed=False``), never an exception.

Registered names (pack id ``dns.<name>``) and params:

``name_resolves``
    ``getent ahosts <name>``: the name resolves through the system resolver
    (``/etc/hosts`` and DNS, as the lab's libc does it). Params: ``name``;
    optional ``expected_address`` or ``expected_addresses`` (every one must
    be among the results); optional ``timeout_seconds``.
``resolver_answers``
    ``dig +short -t <type> <name>``: the DNS server(s) named in
    ``/etc/resolv.conf`` answer the query. Bypasses ``/etc/hosts``, so pinning
    an address locally does not pass. Params: ``name``, ``record_type``
    (``A`` / ``AAAA`` / ``CNAME``), ``expected_value`` or ``expected_values``
    (each must be in the answer); optional ``timeout_seconds``. Needs ``dig``
    in the lab image.
``resolver_config``
    Reads ``/etc/resolv.conf``. Params: at least one of ``nameserver`` (must
    be one of the configured nameservers) and ``search_domains`` (each must
    be in the search list).
``http_status``
    ``curl --url <url>``: an HTTP(S) request from inside the lab (through the
    system resolver) returns ``expected_status``. Params: ``url``,
    ``expected_status``; optional ``timeout_seconds``. Needs ``curl``.
``command_exit``
    Runs ``argv`` and compares its exit code. Params: ``argv``,
    ``expected_exit_code``; optional ``timeout_seconds``.

When a pack gives no ``timeout_seconds`` the adapter's
:data:`CHECK_TIMEOUT_SECONDS` applies; a pack cannot exceed
:data:`MAX_CHECK_TIMEOUT_SECONDS`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass

from domains.dns import _params as p
from harness.core.domain_adapter import AdapterParamsError, CheckObservation
from harness.core.ports.json_types import JsonObject, JsonValue
from harness.core.ports.lab_runtime import ExecRequest, ExecResult, LabRuntime

CHECK_TIMEOUT_SECONDS = 10.0
"""Exec timeout for a check whose params give no ``timeout_seconds``."""
MAX_CHECK_TIMEOUT_SECONDS = 120.0
CHECK_MAX_OUTPUT_BYTES = 64 * 1024
"""Per-stream output cap for every check command."""
OBSERVED_OUTPUT_CHARS = 1000
"""How much of a ``command_exit`` command's stdout/stderr tail is kept as evidence."""

RESOLV_CONF_PATH = "/etc/resolv.conf"
RECORD_TYPES = ("A", "AAAA", "CNAME")


def _timeout(params: JsonObject, where: str) -> float:
    if "timeout_seconds" not in params:
        return CHECK_TIMEOUT_SECONDS
    return p.seconds(
        params["timeout_seconds"], f"{where}.timeout_seconds", maximum=MAX_CHECK_TIMEOUT_SECONDS
    )


def _one_or_many(
    params: JsonObject,
    singular: str,
    plural: str,
    where: str,
    parse: Callable[[JsonValue, str], str],
    *,
    required: bool,
) -> tuple[str, ...]:
    """Read ``singular`` (one value) or ``plural`` (non-empty array), not both."""
    if singular in params and plural in params:
        raise AdapterParamsError(f"{where} has both {singular} and {plural}")
    if singular in params:
        return (parse(params[singular], f"{where}.{singular}"),)
    if plural in params:
        items = p.array(params[plural], f"{where}.{plural}", min_items=1)
        return tuple(parse(v, f"{where}.{plural}[{i}]") for i, v in enumerate(items))
    if required:
        raise AdapterParamsError(f"{where} needs {singular} or {plural}")
    return ()


def _exec(
    lab: LabRuntime, lab_instance_id: str, argv: tuple[str, ...], timeout: float
) -> ExecResult:
    return lab.exec(
        lab_instance_id,
        ExecRequest(
            argv=argv,
            timeout_seconds=timeout,
            max_output_bytes=CHECK_MAX_OUTPUT_BYTES,
            env={},
            workdir=None,
        ),
    )


def _ok(result: ExecResult) -> bool:
    return not result.timed_out and result.exit_code == 0


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _normalize_ip(text: str) -> str | None:
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        return None


# --- name_resolves ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NameResolvesParams:
    name: str
    expected: tuple[str, ...]
    timeout: float

    @classmethod
    def parse(cls, params: JsonObject) -> _NameResolvesParams:
        where = "dns.name_resolves params"
        p.only_keys(
            params,
            where,
            required={"name"},
            optional={"expected_address", "expected_addresses", "timeout_seconds"},
        )
        expected = _one_or_many(
            params, "expected_address", "expected_addresses", where, p.ip, required=False
        )
        return cls(p.dns_name(params["name"], f"{where}.name"), expected, _timeout(params, where))


class NameResolvesCheck:
    """``dns.name_resolves``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _NameResolvesParams.parse(params)

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        args = _NameResolvesParams.parse(params)
        result = _exec(lab, lab_instance_id, ("getent", "ahosts", args.name), args.timeout)
        addresses: set[str] = set()
        for line in _text(result.stdout).splitlines():
            fields = line.split()
            address = _normalize_ip(fields[0]) if fields else None
            if address is not None:
                addresses.add(address)
        passed = _ok(result) and bool(addresses) and set(args.expected) <= addresses
        return CheckObservation(
            passed=passed,
            observed={
                "name": args.name,
                "addresses": sorted(addresses),
                "expected_addresses": list(args.expected),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        )


# --- resolver_answers ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ResolverAnswersParams:
    name: str
    record_type: str
    expected: tuple[str, ...]
    timeout: float

    @classmethod
    def parse(cls, params: JsonObject) -> _ResolverAnswersParams:
        where = "dns.resolver_answers params"
        p.only_keys(
            params,
            where,
            required={"name", "record_type"},
            optional={"expected_value", "expected_values", "timeout_seconds"},
        )
        record_type = p.enum(params["record_type"], f"{where}.record_type", RECORD_TYPES)
        expected = _one_or_many(
            params,
            "expected_value",
            "expected_values",
            where,
            lambda v, w: _parse_value(record_type, v, w),
            required=True,
        )
        name = p.dns_name(params["name"], f"{where}.name")
        return cls(name, record_type, expected, _timeout(params, where))


def _parse_value(record_type: str, value: JsonValue, where: str) -> str:
    if record_type == "CNAME":
        return p.dns_name(value, where)
    address = p.ip(value, where)
    if (":" in address) != (record_type == "AAAA"):
        raise AdapterParamsError(f"{where} is not a {record_type} value")
    return address


def _answer_values(record_type: str, stdout: bytes) -> set[str]:
    """Values of ``record_type`` in ``dig +short`` output (other lines skipped)."""
    values: set[str] = set()
    for line in _text(stdout).splitlines():
        token = line.strip()
        if not token or token.startswith(";"):
            continue
        if record_type == "CNAME":
            values.add(token.lower().rstrip("."))
            continue
        address = _normalize_ip(token)
        if address is not None and (":" in address) == (record_type == "AAAA"):
            values.add(address)
    return values


class ResolverAnswersCheck:
    """``dns.resolver_answers``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _ResolverAnswersParams.parse(params)

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        args = _ResolverAnswersParams.parse(params)
        # dig's own per-try timeout stays inside the exec timeout.
        dig_time = max(1, int(args.timeout / 2))
        argv = (
            "dig",
            "+short",
            f"+time={dig_time}",
            "+tries=1",
            "-t",
            args.record_type,
            "-q",
            args.name,
        )
        result = _exec(lab, lab_instance_id, argv, args.timeout)
        values = _answer_values(args.record_type, result.stdout)
        passed = _ok(result) and bool(values) and set(args.expected) <= values
        return CheckObservation(
            passed=passed,
            observed={
                "name": args.name,
                "record_type": args.record_type,
                "answers": sorted(values),
                "expected_values": list(args.expected),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        )


# --- resolver_config ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ResolverConfigParams:
    nameserver: str | None
    search_domains: tuple[str, ...]

    @classmethod
    def parse(cls, params: JsonObject) -> _ResolverConfigParams:
        where = "dns.resolver_config params"
        p.only_keys(params, where, required=set(), optional={"nameserver", "search_domains"})
        if not params:
            raise AdapterParamsError(f"{where} needs nameserver and/or search_domains")
        nameserver = (
            p.ip(params["nameserver"], f"{where}.nameserver") if "nameserver" in params else None
        )
        search: tuple[str, ...] = ()
        if "search_domains" in params:
            items = p.array(params["search_domains"], f"{where}.search_domains", min_items=1)
            search = tuple(
                p.dns_name(v, f"{where}.search_domains[{i}]") for i, v in enumerate(items)
            )
        return cls(nameserver, search)


def parse_resolv_conf(text: str) -> tuple[list[str], list[str]]:
    """Return (nameservers, search domains) as the resolver reads them.

    ``#`` / ``;`` start comments; the last ``search`` / ``domain`` line wins.
    """
    nameservers: list[str] = []
    search: list[str] = []
    for raw in text.splitlines():
        fields = raw.split("#", 1)[0].split(";", 1)[0].split()
        if len(fields) < 2:
            continue
        if fields[0] == "nameserver":
            address = _normalize_ip(fields[1])
            nameservers.append(address if address is not None else fields[1])
        elif fields[0] in ("search", "domain"):
            search = [f.lower().rstrip(".") for f in fields[1:]]
    return nameservers, search


class ResolverConfigCheck:
    """``dns.resolver_config``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _ResolverConfigParams.parse(params)

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        args = _ResolverConfigParams.parse(params)
        result = _exec(lab, lab_instance_id, ("cat", RESOLV_CONF_PATH), CHECK_TIMEOUT_SECONDS)
        nameservers, search = parse_resolv_conf(_text(result.stdout)) if _ok(result) else ([], [])
        passed = (
            _ok(result)
            and (args.nameserver is None or args.nameserver in nameservers)
            and all(d in search for d in args.search_domains)
        )
        return CheckObservation(
            passed=passed,
            observed={
                "nameservers": list(nameservers),
                "search_domains": list(search),
                "expected_nameserver": args.nameserver,
                "expected_search_domains": list(args.search_domains),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        )


# --- http_status ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _HttpStatusParams:
    url: str
    expected_status: int
    timeout: float

    @classmethod
    def parse(cls, params: JsonObject) -> _HttpStatusParams:
        where = "dns.http_status params"
        p.only_keys(
            params, where, required={"url", "expected_status"}, optional={"timeout_seconds"}
        )
        return cls(
            p.http_url(params["url"], f"{where}.url"),
            p.integer(
                params["expected_status"], f"{where}.expected_status", minimum=100, maximum=599
            ),
            _timeout(params, where),
        )


class HttpStatusCheck:
    """``dns.http_status``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _HttpStatusParams.parse(params)

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        args = _HttpStatusParams.parse(params)
        # curl's own deadline stays inside the exec timeout; "000" means no response.
        max_time = f"{max(1.0, args.timeout - 1):g}"
        argv = ("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", max_time,
                "--url", args.url)  # fmt: skip
        result = _exec(lab, lab_instance_id, argv, args.timeout)
        text = _text(result.stdout).strip()
        status = int(text) if text.isdigit() and text != "000" else None
        return CheckObservation(
            passed=not result.timed_out and status == args.expected_status,
            observed={
                "url": args.url,
                "status": status,
                "expected_status": args.expected_status,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stderr_tail": _tail(result.stderr),
            },
        )


# --- command_exit -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CommandExitParams:
    argv: tuple[str, ...]
    expected_exit_code: int
    timeout: float

    @classmethod
    def parse(cls, params: JsonObject) -> _CommandExitParams:
        where = "dns.command_exit params"
        p.only_keys(
            params, where, required={"argv", "expected_exit_code"}, optional={"timeout_seconds"}
        )
        return cls(
            p.argv(params["argv"], f"{where}.argv"),
            p.integer(
                params["expected_exit_code"], f"{where}.expected_exit_code", minimum=0, maximum=255
            ),
            _timeout(params, where),
        )


def _tail(data: bytes) -> str:
    return _text(data)[-OBSERVED_OUTPUT_CHARS:]


class CommandExitCheck:
    """``dns.command_exit``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _CommandExitParams.parse(params)

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        args = _CommandExitParams.parse(params)
        result = _exec(lab, lab_instance_id, args.argv, args.timeout)
        return CheckObservation(
            passed=not result.timed_out and result.exit_code == args.expected_exit_code,
            observed={
                "argv": list(args.argv),
                "exit_code": result.exit_code,
                "expected_exit_code": args.expected_exit_code,
                "timed_out": result.timed_out,
                "stdout_tail": _tail(result.stdout),
                "stderr_tail": _tail(result.stderr),
            },
        )
