"""Unit tests for the DNS checks, run against FakeLabRuntime."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from domains.dns.checks import (
    CHECK_MAX_OUTPUT_BYTES,
    CHECK_TIMEOUT_SECONDS,
    CommandExitCheck,
    HttpStatusCheck,
    NameResolvesCheck,
    ResolverAnswersCheck,
    ResolverConfigCheck,
    parse_resolv_conf,
)
from harness.core.domain_adapter import AdapterParamsError, Check
from harness.core.ports import ExecRequest, ExecResult, ImageRef, LabNotFoundError, LabSpec
from harness.core.ports.json_types import JsonObject
from harness.core.ports.lab_runtime import ResourceLimits
from harness.testing.fakes import FakeLabRuntime

LAB = "lab-1"


def _result(
    exit_code: int | None, stdout: bytes = b"", stderr: bytes = b"", *, timed_out: bool = False
) -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _lab(handler: Callable[[str, ExecRequest], ExecResult]) -> FakeLabRuntime:
    lab = FakeLabRuntime(handler)
    lab.start(
        LAB,
        LabSpec(
            image=ImageRef(repository="example/lab", digest="sha256:" + "0" * 64),
            limits=ResourceLimits(cpus=1, memory_bytes=1, pids=1, lifetime_seconds=1),
            network="none",
            env={},
            files=(),
            workdir=None,
        ),
    )
    return lab


def _fixed(result: ExecResult) -> FakeLabRuntime:
    return _lab(lambda _id, _req: result)


# --- name_resolves ------------------------------------------------------------

GETENT_OK = (
    b"127.0.10.20     STREAM api.corp.internal\n127.0.10.20     DGRAM  \n"
    b"::ffff:127.0.10.20 RAW    \n"
)


def test_name_resolves_passes_and_uses_getent_argv() -> None:
    lab = _fixed(_result(0, GETENT_OK))
    params = {"name": "API.corp.internal.", "expected_addresses": ["127.0.10.20"]}
    obs = NameResolvesCheck().run(lab, LAB, params)
    assert obs.passed
    assert obs.observed["addresses"] == ["127.0.10.20", "::ffff:127.0.10.20"]
    (_, req), *_ = lab.exec_calls
    assert req.argv == ("getent", "ahosts", "api.corp.internal")
    assert req.timeout_seconds == CHECK_TIMEOUT_SECONDS
    assert req.max_output_bytes == CHECK_MAX_OUTPUT_BYTES


def test_name_resolves_fails_when_lookup_fails() -> None:
    obs = NameResolvesCheck().run(_fixed(_result(2)), LAB, {"name": "api.corp.internal"})
    assert not obs.passed
    assert obs.observed["exit_code"] == 2


def test_name_resolves_fails_on_wrong_address_or_timeout() -> None:
    params = {"name": "api.corp.internal", "expected_addresses": ["127.0.10.21"]}
    assert not NameResolvesCheck().run(_fixed(_result(0, GETENT_OK)), LAB, params).passed
    timed_out = _result(None, GETENT_OK, timed_out=True)
    assert not NameResolvesCheck().run(_fixed(timed_out), LAB, {"name": "a.b"}).passed


def test_name_resolves_uses_pack_timeout() -> None:
    lab = _fixed(_result(0, GETENT_OK))
    NameResolvesCheck().run(lab, LAB, {"name": "a.b", "timeout_seconds": 5})
    assert lab.exec_calls[0][1].timeout_seconds == 5.0


# --- resolver_answers ---------------------------------------------------------


def test_resolver_answers_passes_and_uses_dig_argv() -> None:
    lab = _fixed(_result(0, b"127.0.10.20\n"))
    params = {
        "name": "api.corp.internal",
        "record_type": "A",
        "expected_values": ["127.0.10.20"],
        "timeout_seconds": 5,
    }
    obs = ResolverAnswersCheck().run(lab, LAB, params)
    assert obs.passed
    assert obs.observed["answers"] == ["127.0.10.20"]
    req = lab.exec_calls[0][1]
    assert req.argv == (
        "dig", "+short", "+time=2", "+tries=1", "-t", "A", "-q", "api.corp.internal",
    )  # fmt: skip
    assert req.timeout_seconds == 5.0


def test_resolver_answers_fails_when_no_server_answers() -> None:
    out = b";; communications error to 192.0.2.53#53: timed out\n;; no servers could be reached\n"
    params = {"name": "api.corp.internal", "record_type": "A", "expected_values": ["127.0.10.20"]}
    obs = ResolverAnswersCheck().run(_fixed(_result(9, out)), LAB, params)
    assert not obs.passed
    assert obs.observed["answers"] == []


def test_resolver_answers_ignores_cname_lines_for_a_queries() -> None:
    out = b"alias.corp.internal.\n127.0.10.20\n"
    params = {"name": "www.corp.internal", "record_type": "A", "expected_values": ["127.0.10.20"]}
    obs = ResolverAnswersCheck().run(_fixed(_result(0, out)), LAB, params)
    assert obs.passed and obs.observed["answers"] == ["127.0.10.20"]
    cname = {"name": "www.corp.internal", "record_type": "CNAME",
             "expected_values": ["Alias.corp.internal."]}  # fmt: skip
    assert ResolverAnswersCheck().run(_fixed(_result(0, out)), LAB, cname).passed


def test_resolver_answers_fails_on_nxdomain_empty_output() -> None:
    params = {"name": "api.corp.internal", "record_type": "A", "expected_values": ["127.0.10.20"]}
    assert not ResolverAnswersCheck().run(_fixed(_result(0, b"")), LAB, params).passed


# --- resolver_config ----------------------------------------------------------

RESOLV = b"# comment\nnameserver 192.0.2.53\nnameserver 127.0.0.53\nsearch Corp.internal.\n"


def test_parse_resolv_conf() -> None:
    assert parse_resolv_conf(RESOLV.decode()) == (["192.0.2.53", "127.0.0.53"], ["corp.internal"])
    assert parse_resolv_conf("search a\ndomain b\n")[1] == ["b"]


def test_resolver_config() -> None:
    lab = _fixed(_result(0, RESOLV))
    assert ResolverConfigCheck().run(lab, LAB, {"nameserver": "127.0.0.53"}).passed
    assert lab.exec_calls[0][1].argv == ("cat", "/etc/resolv.conf")
    assert ResolverConfigCheck().run(lab, LAB, {"search_domains": ["corp.internal"]}).passed
    obs = ResolverConfigCheck().run(lab, LAB, {"nameserver": "127.0.0.1"})
    assert not obs.passed
    assert obs.observed["nameservers"] == ["192.0.2.53", "127.0.0.53"]
    assert not ResolverConfigCheck().run(_fixed(_result(1)), LAB, {"nameserver": "1.1.1.1"}).passed


# --- singular / plural expected values ---------------------------------------


def test_singular_expected_value_aliases() -> None:
    lab = _fixed(_result(0, b"127.0.10.20\n"))
    single = {"name": "a.b", "record_type": "A", "expected_value": "127.0.10.20"}
    assert ResolverAnswersCheck().run(lab, LAB, single).passed
    wrong = {"name": "a.b", "record_type": "A", "expected_value": "127.0.10.21"}
    assert not ResolverAnswersCheck().run(lab, LAB, wrong).passed
    lab = _fixed(_result(0, GETENT_OK))
    assert (
        NameResolvesCheck().run(lab, LAB, {"name": "a.b", "expected_address": "127.0.10.20"}).passed
    )


# --- http_status --------------------------------------------------------------


def test_http_status() -> None:
    lab = _fixed(_result(0, b"200"))
    params = {"url": "http://api.corp.internal:8080/health", "expected_status": 200,
              "timeout_seconds": 5}  # fmt: skip
    obs = HttpStatusCheck().run(lab, LAB, params)
    assert obs.passed and obs.observed["status"] == 200
    req = lab.exec_calls[0][1]
    assert req.argv == (
        "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "4",
        "--url", "http://api.corp.internal:8080/health",
    )  # fmt: skip
    assert req.timeout_seconds == 5.0
    assert not HttpStatusCheck().run(_fixed(_result(0, b"404")), LAB, params).passed
    no_answer = HttpStatusCheck().run(_fixed(_result(6, b"000")), LAB, params)
    assert not no_answer.passed and no_answer.observed["status"] is None


# --- command_exit -------------------------------------------------------------


def test_command_exit() -> None:
    lab = _fixed(_result(0, b"ok"))
    params = {"argv": ["curl", "-fsS", "http://api/health"], "expected_exit_code": 0}
    obs = CommandExitCheck().run(lab, LAB, params)
    assert obs.passed and obs.observed["stdout_tail"] == "ok"
    assert lab.exec_calls[0][1].argv == ("curl", "-fsS", "http://api/health")
    assert not CommandExitCheck().run(_fixed(_result(6)), LAB, params).passed
    assert not CommandExitCheck().run(_fixed(_result(None, timed_out=True)), LAB, params).passed


def test_lab_errors_propagate() -> None:
    lab = FakeLabRuntime()
    with pytest.raises(LabNotFoundError):
        CommandExitCheck().run(lab, "missing", {"argv": ["true"], "expected_exit_code": 0})


# --- params validation --------------------------------------------------------

INVALID: list[tuple[Check, JsonObject]] = [
    (NameResolvesCheck(), {}),
    (NameResolvesCheck(), {"name": "-rf"}),
    (NameResolvesCheck(), {"name": "a b"}),
    (NameResolvesCheck(), {"name": "a.b", "extra": 1}),
    (NameResolvesCheck(), {"name": "a.b", "expected_addresses": []}),
    (NameResolvesCheck(), {"name": "a.b", "expected_addresses": ["not-an-ip"]}),
    (NameResolvesCheck(), {"name": "a.b", "timeout_seconds": 0}),
    (NameResolvesCheck(), {"name": "a.b", "timeout_seconds": 1000}),
    (NameResolvesCheck(), {"name": "a.b", "timeout_seconds": True}),
    (ResolverAnswersCheck(), {"name": "a.b", "record_type": "MX", "expected_values": ["x"]}),
    (ResolverAnswersCheck(), {"name": "a.b", "record_type": "A", "expected_values": ["::1"]}),
    (ResolverAnswersCheck(), {"name": "a", "record_type": "AAAA", "expected_values": ["1.2.3.4"]}),
    (ResolverAnswersCheck(), {"name": "a.b", "record_type": "A"}),
    (
        ResolverAnswersCheck(),
        {"name": "a", "record_type": "A", "expected_value": "1.2.3.4", "expected_values": []},
    ),
    (NameResolvesCheck(), {"name": "a.b", "expected_address": ["1.2.3.4"]}),
    (HttpStatusCheck(), {"url": "ftp://a/b", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "http:///nohost", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "http://u:p@a/", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "http://a:99999/", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "http://a/ b", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "-K/etc/passwd", "expected_status": 200}),
    (HttpStatusCheck(), {"url": "http://a/", "expected_status": 99}),
    (ResolverConfigCheck(), {}),
    (ResolverConfigCheck(), {"nameserver": "x"}),
    (CommandExitCheck(), {"argv": [], "expected_exit_code": 0}),
    (CommandExitCheck(), {"argv": "curl http://x", "expected_exit_code": 0}),
    (CommandExitCheck(), {"argv": ["curl http://x"], "expected_exit_code": 0}),
    (CommandExitCheck(), {"argv": ["true"], "expected_exit_code": 256}),
    (CommandExitCheck(), {"argv": ["true"], "expected_exit_code": False}),
]


@pytest.mark.parametrize(("check", "params"), INVALID)
def test_invalid_params_rejected(check: Check, params: JsonObject) -> None:
    with pytest.raises(AdapterParamsError):
        check.validate_params(params)


def test_contents_pack_check_params_are_valid() -> None:
    # Mirrors the contents/software-engineering activity
    # gen-diagnose-dns-resolver-misconfiguration-001.json.
    ResolverAnswersCheck().validate_params(
        {
            "name": "api.corp.internal",
            "record_type": "A",
            "expected_value": "127.0.10.20",
            "timeout_seconds": 5,
        }
    )
    NameResolvesCheck().validate_params(
        {"name": "api.corp.internal", "expected_address": "127.0.10.20", "timeout_seconds": 5}
    )
    HttpStatusCheck().validate_params(
        {
            "url": "http://api.corp.internal:8080/health",
            "expected_status": 200,
            "timeout_seconds": 5,
        }
    )
    ResolverAnswersCheck().validate_params(
        {
            "name": "api.corp.internal",
            "record_type": "A",
            "expected_values": ["127.0.10.20"],
            "timeout_seconds": 5,
        }
    )
    NameResolvesCheck().validate_params(
        {"name": "api.corp.internal", "expected_addresses": ["127.0.10.20"], "timeout_seconds": 5}
    )
    CommandExitCheck().validate_params(
        {
            "argv": ["curl", "-fsS", "--max-time", "5", "http://api.corp.internal:8080/health"],
            "expected_exit_code": 0,
            "timeout_seconds": 10,
        }
    )
