"""Unit tests for the dns.resolver_lab fixture provider, the lab daemon's DNS
answers, and registering the adapter in the core registry."""

from __future__ import annotations

import copy
import json
import socket
import struct
from typing import Any

import pytest
from dns_lab_testdata import FAULT_PARAMS, IMAGE, PARAMS

import domains.dns
from domains.dns.fixtures import (
    BOOT_PATH,
    CONFIG_PATH,
    INITIAL_RESOLV_CONF_PATH,
    LAB_NETWORK,
    LAB_RESOURCE_LIMITS,
    LABD_PATH,
    ResolverLabFixture,
    explicit_params,
)
from domains.dns.sandbox import labd
from harness.core.domain_adapter import (
    AdapterParamsError,
    DomainAdapterRegistry,
    ItemReference,
    SemVer,
)


def _files(params: dict[str, Any]) -> dict[str, bytes]:
    spec = ResolverLabFixture().build_lab_spec(IMAGE, params)
    return {f.path: f.content for f in spec.files}


def test_build_lab_spec() -> None:
    spec = ResolverLabFixture().build_lab_spec(IMAGE, PARAMS)
    assert spec.image is IMAGE
    assert spec.limits == LAB_RESOURCE_LIMITS
    assert spec.network == "none"
    files = {f.path: f for f in spec.files}
    assert files[INITIAL_RESOLV_CONF_PATH].content == (
        b"nameserver 192.0.2.53\noptions timeout:1 attempts:1\n"
    )
    assert files[BOOT_PATH].mode == 0o755 and files[LABD_PATH].mode == 0o755
    assert files[LABD_PATH].content.startswith(b'"""Lab daemon')
    config = json.loads(files[CONFIG_PATH].content)
    assert config["zone_server"]["records"][0] == {
        "name": "api.corp.internal",
        "type": "A",
        "address": "127.0.10.20",
    }
    assert config["services"][0]["port"] == 8080
    # The main shell's startup hook runs boot (see fixtures module docstring).
    hook = spec.env["ZDOTDIR"] + "/.zshenv"
    assert BOOT_PATH.encode() in files[hook].content


def test_build_lab_spec_is_deterministic() -> None:
    fixture = ResolverLabFixture()
    assert fixture.build_lab_spec(IMAGE, PARAMS) == fixture.build_lab_spec(
        IMAGE, copy.deepcopy(PARAMS)
    )


def test_pack_values_never_reach_the_boot_script() -> None:
    params = copy.deepcopy(PARAMS)
    params["resolv_conf"]["search"] = ["corp.internal"]
    params["network"] = "isolated"
    spec = ResolverLabFixture().build_lab_spec(IMAGE, params)
    assert spec.network == "isolated"
    files = {f.path: f.content for f in spec.files}
    assert files[BOOT_PATH] == _files(PARAMS)[BOOT_PATH]
    assert b"search corp.internal\n" in files[INITIAL_RESOLV_CONF_PATH]


def test_network_defaults_to_module_constant() -> None:
    params = {k: v for k, v in PARAMS.items() if k != "network"}
    assert ResolverLabFixture().build_lab_spec(IMAGE, params).network == LAB_NETWORK


def test_fault_form_translates_to_explicit_form() -> None:
    explicit = dict(explicit_params(FAULT_PARAMS))
    assert explicit["resolv_conf"] == {
        "nameservers": ["192.0.2.53"],
        "search": [],
        "options": ["timeout:1", "attempts:1"],
    }
    fixture = ResolverLabFixture()
    spec = fixture.build_lab_spec(IMAGE, FAULT_PARAMS)
    assert spec == fixture.build_lab_spec(IMAGE, explicit)
    files = {f.path: f.content for f in spec.files}
    assert files[INITIAL_RESOLV_CONF_PATH] == _files(PARAMS)[INITIAL_RESOLV_CONF_PATH]
    config = json.loads(files[CONFIG_PATH])
    assert config["zone_server"]["listen_address"] == "127.0.0.53"
    assert config["services"][0]["port"] == 8080


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("fault", "missing_record"),
        ("service_name", "api.other.internal"),
        ("configured_nameserver", "127.0.0.53"),
        ("resolver_attempts", 0),
        ("service_port", "8080"),
        ("extra", 1),
    ],
)
def test_invalid_fault_form_rejected(key: str, value: Any) -> None:
    with pytest.raises(AdapterParamsError):
        ResolverLabFixture().validate_params({**FAULT_PARAMS, key: value})


def _mutate(path: list[str | int], value: Any) -> dict[str, Any]:
    params = copy.deepcopy(PARAMS)
    target: Any = params
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return params


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["network"], "host"),
        (["zone_server"], _DELETE),
        (["zone_server", "listen_address"], "::1"),
        (["zone_server", "port"], 0),
        (["zone_server", "records"], []),
        (["zone_server", "records", 0, "type"], "AAAA"),
        (["zone_server", "records", 0, "address"], "127.0.10"),
        (["zone_server", "records", 0, "name"], "bad name"),
        (["services", 0, "port"], 70000),
        (["services", 0, "health_path"], "health"),
        (["services", 0, "health_body"], 1),
        (["services", 0, "extra"], True),
        (["resolv_conf", "nameservers"], []),
        (["resolv_conf", "nameservers"], ["1.1.1.1", "1.1.1.2", "1.1.1.3", "1.1.1.4"]),
        (["resolv_conf", "options"], ["timeout:1; rm -rf /"]),
        (["resolv_conf", "search"], ["-x"]),
        (["unknown"], 1),
    ],
)
def test_invalid_params_rejected(path: list[str | int], value: Any) -> None:
    with pytest.raises(AdapterParamsError):
        ResolverLabFixture().validate_params(_mutate(path, value))


def test_duplicate_service_endpoint_rejected() -> None:
    params = copy.deepcopy(PARAMS)
    params["services"].append(dict(params["services"][0]))
    with pytest.raises(AdapterParamsError, match="reuses"):
        ResolverLabFixture().validate_params(params)


# --- labd DNS answers (the code that runs inside the lab) ---------------------


def _query(name: str, qtype: int = 1, ident: int = 0x1234) -> bytes:
    qname = b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0"
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", qtype, 1)


RECORDS = {"api.corp.internal": [socket.inet_aton("127.0.10.20")]}


def test_labd_answers_a_record() -> None:
    response = labd.answer(_query("API.corp.internal"), RECORDS, 60)
    assert response is not None
    ident, flags, qd, an, _, _ = struct.unpack("!HHHHHH", response[:12])
    assert (ident, flags & 0x8000, flags & 0xF, qd, an) == (0x1234, 0x8000, 0, 1, 1)
    assert response.endswith(struct.pack("!IH", 60, 4) + socket.inet_aton("127.0.10.20"))


def test_labd_nxdomain_nodata_and_garbage() -> None:
    nx = labd.answer(_query("nope.corp.internal"), RECORDS, 60)
    assert nx is not None and struct.unpack("!H", nx[2:4])[0] & 0xF == 3
    nodata = labd.answer(_query("api.corp.internal", qtype=28), RECORDS, 60)
    assert nodata is not None
    assert struct.unpack("!HH", nodata[2:4] + nodata[6:8]) == (0x8580, 0)
    assert labd.answer(b"\x00", RECORDS, 60) is None
    formerr = labd.answer(_query("a")[:14], RECORDS, 60)
    assert formerr is not None and struct.unpack("!H", formerr[2:4])[0] & 0xF == 1


# --- adapter + registry -------------------------------------------------------


def test_adapter_registers_and_verifies_contents_pack_references() -> None:
    adapter = domains.dns.adapter()
    assert adapter is domains.dns.ADAPTER
    assert adapter.adapter_id == "dns"
    SemVer.parse(adapter.version)
    registry = DomainAdapterRegistry()
    registry.register(adapter)
    check_params: dict[str, dict[str, Any]] = {
        "dns.resolver_answers": {
            "name": "api.corp.internal",
            "record_type": "A",
            "expected_values": ["127.0.10.20"],
        },
        "dns.name_resolves": {"name": "api.corp.internal"},
        "dns.resolver_config": {"nameserver": "127.0.0.53"},
        "dns.http_status": {"url": "http://api.corp.internal:8080/health", "expected_status": 200},
        "dns.command_exit": {"argv": ["true"], "expected_exit_code": 0},
    }
    refs = [
        ItemReference(kind="fixture", item_id="dns.resolver_lab", params=PARAMS, source="env"),
        ItemReference(kind="tool", item_id="dns.terminal", params=None, source="activity"),
        *(
            ItemReference(kind="check", item_id=item_id, params=params, source=item_id)
            for item_id, params in check_params.items()
        ),
    ]
    assert registry.verify({"dns": ">=0.1.0,<0.2.0"}, refs) == []
    assert sorted(adapter.fixtures) == ["resolver_lab"]
    assert sorted(adapter.checks) == sorted(k.removeprefix("dns.") for k in check_params)
    assert sorted(adapter.tools) == ["terminal"]
