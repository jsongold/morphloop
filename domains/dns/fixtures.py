"""DNS environment fixture provider ``dns.resolver_lab`` (ADR-0009).

Turns an EnvironmentDefinition's ``params`` into a :class:`LabSpec` for a
self-contained, loopback-only DNS lab:

- an authoritative DNS server for the pack's A records (``zone_server``),
- zero or more HTTP services with a health endpoint (``services``),
- the (typically broken) stub-resolver configuration ``/etc/resolv.conf``
  the learner starts from (``resolv_conf``).

The fault lives entirely in the pack's params (e.g. ``resolv_conf`` naming an
address where no DNS server runs); this provider only renders them. The same
params always give the same spec, so reset (which calls
:meth:`ResolverLabFixture.build_lab_spec` again) restores the same state.

Params come in one of two equivalent forms. The *explicit* form (all
required unless marked optional)::

    network        optional, "none" | "isolated"; default LAB_NETWORK
    zone_server    {listen_address: IPv4, port: 1-65535, ttl_seconds: 0-86400,
                    records: [{name: host name, type: "A", address: IPv4}, ...]}
    services       [{name: host name, listen_address: IPv4, port: 1-65535,
                     health_path: "/...", health_body: string}, ...]  (may be [])
    resolv_conf    {nameservers: [IP, ...] (1-3), search: [domain, ...],
                    options: ["timeout:1", ...]}

The *fault* form (selected by the presence of ``fault``) names one service and
one fault, and is translated into the explicit form by
:func:`explicit_params`::

    zone, service_name (inside zone), service_address, service_port,
    health_path, dns_server_address (port 53), record_ttl_seconds,
    resolver_timeout_seconds, resolver_attempts, optional network, and
    fault "wrong_nameserver" + configured_nameserver (the address the
    resolver is wrongly pointed at).

The health endpoint answers :data:`FAULT_FORM_HEALTH_BODY` in that form.

How the lab comes up. The spec names its own main process,
``/bin/sh /opt/lab/bin/boot`` (:data:`LAB_COMMAND`): ``boot`` overwrites
``/etc/resolv.conf`` in place (Docker bind-mounts it), then ``exec``s
``labd.py`` (``domains/dns/sandbox/labd.py``), which serves the zone and the
services for the life of the lab. The spec also declares a readiness probe
(:data:`LAB_READINESS`) on ``/opt/lab/run/ready``, the file ``labd`` creates
once every socket is bound, so the runtime returns from ``start`` only when
the lab really answers. The learner's interactive shell is a separate
session opened by the terminal bridge, not this process.

Image requirements: ``/bin/sh`` and ``test``; ``python3`` >= 3.8 (runs
``labd.py``); ``getent`` and ``dig`` for the checks; ``bash`` >= 4.4, ``od``
and ``tr`` for the terminal tool.

Resource limits and the default network live here as module constants for
v0.1, because ``environment.json`` has no field for them (their final home is
an open decision): :data:`LAB_RESOURCE_LIMITS`, :data:`LAB_NETWORK`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from domains.dns import _params as p
from harness.core.domain_adapter import AdapterParamsError
from harness.core.ports.json_types import JsonObject, JsonValue, PlainJson
from harness.core.ports.lab_runtime import (
    ImageRef,
    LabFile,
    LabSpec,
    NetworkMode,
    ReadinessProbe,
    ResourceLimits,
)

LAB_RESOURCE_LIMITS = ResourceLimits(
    cpus=1.0,
    memory_bytes=256 * 1024 * 1024,
    pids=256,
    lifetime_seconds=2 * 60 * 60,
)
"""Hard limits of every ``dns.resolver_lab`` lab (v0.1 home; see module docstring)."""

LAB_NETWORK: NetworkMode = "none"
"""Network of a lab whose params give no ``network``: loopback only. The lab
needs nothing else: its DNS server and services all listen on loopback."""

LAB_ROOT = "/opt/lab"
BOOT_PATH = f"{LAB_ROOT}/bin/boot"
LABD_PATH = f"{LAB_ROOT}/bin/labd.py"
CONFIG_PATH = f"{LAB_ROOT}/lab.json"
INITIAL_RESOLV_CONF_PATH = f"{LAB_ROOT}/resolv.conf.initial"
RUN_DIR = f"{LAB_ROOT}/run"
READY_PATH = f"{RUN_DIR}/ready"

LAB_COMMAND = ("/bin/sh", BOOT_PATH)
"""Main process of every ``dns.resolver_lab`` lab: the boot script, which
ends by ``exec``-ing the lab daemon."""

LAB_READINESS = ReadinessProbe(
    argv=("test", "-e", READY_PATH),
    timeout_seconds=30.0,
    interval_seconds=0.2,
)
"""Ready when ``labd`` has bound the zone server and every service socket:
it creates :data:`READY_PATH` only then (``domains/dns/sandbox/labd.py``)."""

FAULT_FORM_HEALTH_BODY = "ok\n"
FAULT_FORM_DNS_PORT = 53
FAULTS = ("wrong_nameserver",)

_MAX_NAMESERVERS = 3  # resolvers ignore more (MAXNS)
_MAX_HEALTH_BODY = 4096

# Runs inside the lab only, as its main process. Fixed text: no pack-derived
# value is interpolated.
_BOOT_SCRIPT = f"""#!/bin/sh
# Main process of a dns.resolver_lab lab instance.
set -e
mkdir -p {RUN_DIR}
# /etc/resolv.conf is bind-mounted by Docker: overwrite it in place.
cat {INITIAL_RESOLV_CONF_PATH} > /etc/resolv.conf
exec python3 {LABD_PATH} {CONFIG_PATH} </dev/null >{RUN_DIR}/labd.log 2>&1
""".encode()

_LABD_SOURCE = resources.files("domains.dns.sandbox").joinpath("labd.py").read_bytes()


@dataclass(frozen=True, slots=True)
class _ResolverLabParams:
    network: NetworkMode
    lab_config: dict[str, PlainJson]
    resolv_conf: str

    @classmethod
    def parse(cls, params: JsonObject) -> _ResolverLabParams:
        where = "dns.resolver_lab params"
        if "fault" in params:
            params = explicit_params(params)
        p.only_keys(
            params, where, required={"zone_server", "services", "resolv_conf"}, optional={"network"}
        )
        network: NetworkMode = LAB_NETWORK
        if "network" in params:
            network = (
                "none"
                if p.enum(params["network"], f"{where}.network", ("none", "isolated")) == "none"
                else "isolated"
            )
        zone = _zone_server(params["zone_server"], f"{where}.zone_server")
        services = _services(params["services"], f"{where}.services")
        config: dict[str, PlainJson] = {
            "zone_server": zone,
            "services": services,
            "ready_file": READY_PATH,
        }
        return cls(network, config, _resolv_conf(params["resolv_conf"], f"{where}.resolv_conf"))


def explicit_params(params: JsonObject) -> JsonObject:
    """Translate the fault form of the params into the explicit form."""
    where = "dns.resolver_lab params"
    required = {
        "zone", "service_name", "service_address", "service_port", "health_path",
        "dns_server_address", "record_ttl_seconds", "fault", "configured_nameserver",
        "resolver_timeout_seconds", "resolver_attempts",
    }  # fmt: skip
    p.only_keys(params, where, required=required, optional={"network"})
    p.enum(params["fault"], f"{where}.fault", FAULTS)
    zone = p.dns_name(params["zone"], f"{where}.zone")
    service_name = p.dns_name(params["service_name"], f"{where}.service_name")
    if not service_name.endswith("." + zone):
        raise AdapterParamsError(f"{where}.service_name must be inside zone {zone!r}")
    dns_server = p.ipv4(params["dns_server_address"], f"{where}.dns_server_address")
    wrong = p.ip(params["configured_nameserver"], f"{where}.configured_nameserver")
    if wrong == dns_server:
        raise AdapterParamsError(f"{where}.configured_nameserver must differ from the DNS server")
    timeout = p.integer(params["resolver_timeout_seconds"], f"{where}.resolver_timeout_seconds",
                        minimum=1, maximum=30)  # fmt: skip
    attempts = p.integer(params["resolver_attempts"], f"{where}.resolver_attempts",
                         minimum=1, maximum=5)  # fmt: skip
    explicit: dict[str, JsonValue] = {
        "zone_server": {
            "listen_address": dns_server,
            "port": FAULT_FORM_DNS_PORT,
            "ttl_seconds": params["record_ttl_seconds"],
            "records": [{"name": service_name, "type": "A", "address": params["service_address"]}],
        },
        "services": [
            {
                "name": service_name,
                "listen_address": params["service_address"],
                "port": params["service_port"],
                "health_path": params["health_path"],
                "health_body": FAULT_FORM_HEALTH_BODY,
            }
        ],
        "resolv_conf": {
            "nameservers": [wrong],
            "search": [],
            "options": [f"timeout:{timeout}", f"attempts:{attempts}"],
        },
    }
    if "network" in params:
        explicit["network"] = params["network"]
    return explicit


def _zone_server(value: JsonValue, where: str) -> dict[str, PlainJson]:
    zone = p.obj(value, where)
    p.only_keys(
        zone, where, required={"listen_address", "port", "ttl_seconds", "records"}, optional=set()
    )
    records: list[PlainJson] = []
    for i, item in enumerate(p.array(zone["records"], f"{where}.records", min_items=1)):
        rwhere = f"{where}.records[{i}]"
        record = p.obj(item, rwhere)
        p.only_keys(record, rwhere, required={"name", "type", "address"}, optional=set())
        p.enum(record["type"], f"{rwhere}.type", ("A",))
        records.append(
            {
                "name": p.dns_name(record["name"], f"{rwhere}.name"),
                "type": "A",
                "address": p.ipv4(record["address"], f"{rwhere}.address"),
            }
        )
    return {
        "listen_address": p.ipv4(zone["listen_address"], f"{where}.listen_address"),
        "port": p.integer(zone["port"], f"{where}.port", minimum=1, maximum=65535),
        "ttl_seconds": p.integer(
            zone["ttl_seconds"], f"{where}.ttl_seconds", minimum=0, maximum=86400
        ),
        "records": records,
    }


def _services(value: JsonValue, where: str) -> list[PlainJson]:
    services: list[PlainJson] = []
    endpoints: set[tuple[str, int]] = set()
    for i, item in enumerate(p.array(value, where, min_items=0)):
        swhere = f"{where}[{i}]"
        service = p.obj(item, swhere)
        p.only_keys(
            service,
            swhere,
            required={"name", "listen_address", "port", "health_path", "health_body"},
            optional=set(),
        )
        address = p.ipv4(service["listen_address"], f"{swhere}.listen_address")
        port = p.integer(service["port"], f"{swhere}.port", minimum=1, maximum=65535)
        if (address, port) in endpoints:
            raise AdapterParamsError(f"{swhere} reuses {address}:{port}")
        endpoints.add((address, port))
        body = service["health_body"]
        if not isinstance(body, str) or len(body) > _MAX_HEALTH_BODY:
            raise AdapterParamsError(
                f"{swhere}.health_body must be a string of at most {_MAX_HEALTH_BODY} chars"
            )
        services.append(
            {
                "name": p.dns_name(service["name"], f"{swhere}.name"),
                "listen_address": address,
                "port": port,
                "health_path": p.http_path(service["health_path"], f"{swhere}.health_path"),
                "health_body": body,
            }
        )
    return services


def _resolv_conf(value: JsonValue, where: str) -> str:
    conf = p.obj(value, where)
    p.only_keys(conf, where, required={"nameservers", "search", "options"}, optional=set())
    nameservers = [
        p.ip(v, f"{where}.nameservers[{i}]")
        for i, v in enumerate(p.array(conf["nameservers"], f"{where}.nameservers", min_items=1))
    ]
    if len(nameservers) > _MAX_NAMESERVERS:
        raise AdapterParamsError(f"{where}.nameservers has more than {_MAX_NAMESERVERS} entries")
    search = [
        p.dns_name(v, f"{where}.search[{i}]")
        for i, v in enumerate(p.array(conf["search"], f"{where}.search", min_items=0))
    ]
    options = [
        p.resolv_option(v, f"{where}.options[{i}]")
        for i, v in enumerate(p.array(conf["options"], f"{where}.options", min_items=0))
    ]
    lines = [f"nameserver {ns}" for ns in nameservers]
    if search:
        lines.append("search " + " ".join(search))
    if options:
        lines.append("options " + " ".join(options))
    return "\n".join(lines) + "\n"


class ResolverLabFixture:
    """``dns.resolver_lab``: see the module docstring."""

    def validate_params(self, params: JsonObject) -> None:
        _ResolverLabParams.parse(params)

    def build_lab_spec(self, image: ImageRef, params: JsonObject) -> LabSpec:
        args = _ResolverLabParams.parse(params)
        config = json.dumps(args.lab_config, sort_keys=True, indent=2).encode() + b"\n"
        return LabSpec(
            image=image,
            limits=LAB_RESOURCE_LIMITS,
            network=args.network,
            env={},
            files=(
                LabFile(path=CONFIG_PATH, content=config, mode=0o644),
                LabFile(
                    path=INITIAL_RESOLV_CONF_PATH, content=args.resolv_conf.encode(), mode=0o644
                ),
                LabFile(path=LABD_PATH, content=_LABD_SOURCE, mode=0o755),
                LabFile(path=BOOT_PATH, content=_BOOT_SCRIPT, mode=0o755),
            ),
            workdir=None,
            command=LAB_COMMAND,
            readiness=LAB_READINESS,
        )
