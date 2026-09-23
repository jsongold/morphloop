"""Shared fixture pieces for the CLI tests: the contract DNS pack and fakes.

The pack is the same one the Importer tests use
(``tests/contracts/fixtures/pack/valid/dns-pack/``), served in memory, with a
fake ``dns`` domain adapter registering exactly the items its Template allows.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.cli.pack_files import PackWriter
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    AdapterParamsError,
    CheckObservation,
    DomainAdapterRegistry,
)
from harness.core.pack import PackImporter
from harness.core.ports import (
    ExecRequest,
    ExecResult,
    JsonObject,
    LabRuntime,
    PackSource,
    ResourceLimits,
)
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeTerminalTool,
    InMemoryEventStore,
    InMemoryPackSource,
)

DNS_PACK = Path(__file__).resolve().parent.parent / "contracts/fixtures/pack/valid/dns-pack"
LOCATION = "packs/dns"
TEMPLATE_ID = "diagnose-dns-failure"
SCHEMAS = ContractSchemas.load()

SERVICE_NAME = "api.internal"
FIX_ARGV = ("sh", "-c", "printf 'nameserver 127.0.0.53\\n' > /etc/resolv.conf")

Files = dict[str, bytes]


# --- the pack ---------------------------------------------------------------


def pack_files() -> Files:
    return {
        path.relative_to(DNS_PACK).as_posix(): path.read_bytes()
        for path in sorted(DNS_PACK.rglob("*"))
        if path.is_file()
    }


def load(files: Files, path: str) -> Any:
    return json.loads(files[path])


def importer_factory(
    adapters: DomainAdapterRegistry,
) -> Callable[[PackSource], PackImporter]:
    """A factory of :class:`PackImporter` over any pack source (no DB needed)."""

    def build(source: PackSource) -> PackImporter:
        return PackImporter(
            source=source,
            store=InMemoryEventStore(),
            schemas=SCHEMAS,
            adapters=adapters,
            algorithms=v01_algorithm_registry(),
        )

    return build


def pack_source(files: Files) -> InMemoryPackSource:
    return InMemoryPackSource({LOCATION: files})


# --- the domain adapter -----------------------------------------------------


class NameResolvesCheck:
    """Passes when ``getent ahosts <name>`` exits 0 inside the lab."""

    def validate_params(self, params: JsonObject) -> None:
        if not isinstance(params.get("name"), str):
            raise AdapterParamsError("params.name must be a string")

    def run(self, lab: LabRuntime, lab_instance_id: str, params: JsonObject) -> CheckObservation:
        name = params["name"]
        assert isinstance(name, str)
        result = lab.exec(
            lab_instance_id,
            ExecRequest(
                argv=("getent", "ahosts", name),
                timeout_seconds=5,
                max_output_bytes=4096,
                env={},
                workdir=None,
            ),
        )
        return CheckObservation(passed=result.exit_code == 0, observed={"name": name})


def adapters() -> DomainAdapterRegistry:
    registry = DomainAdapterRegistry()
    limits = ResourceLimits(cpus=0.5, memory_bytes=2**28, pids=128, lifetime_seconds=3600)
    registry.register(
        FakeDomainAdapter(
            adapter_id="dns",
            version="0.1.2",
            fixtures={
                "broken-resolver": FakeFixtureProvider(
                    limits=limits, network="isolated", required_params=("fault",)
                )
            },
            checks={
                "name_resolves": NameResolvesCheck(),
                "command_exit": FakeCommandExitCheck(timeout_seconds=10, max_output_bytes=4096),
            },
            tools={"terminal": FakeTerminalTool()},
        )
    )
    return registry


# --- the lab ----------------------------------------------------------------


def _result(exit_code: int) -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        stdout=b"",
        stderr=b"" if exit_code == 0 else b"resolution failed",
        timed_out=False,
        stdout_truncated=False,
        stderr_truncated=False,
    )


class ResolverLab:
    """Exec handler: name resolution fails until :data:`FIX_ARGV` has run in that lab."""

    def __init__(self, *, fix_argv: tuple[str, ...] = FIX_ARGV) -> None:
        self.fixed: set[str] = set()
        self._fix_argv = fix_argv

    def __call__(self, lab_instance_id: str, request: ExecRequest) -> ExecResult:
        if request.argv == self._fix_argv:
            self.fixed.add(lab_instance_id)
            return _result(0)
        if request.argv[0] == "getent":
            return _result(0 if lab_instance_id in self.fixed else 2)
        return _result(0)


# --- the writer -------------------------------------------------------------


class MemoryPackWriter:
    """:class:`~harness.cli.pack_files.PackWriter` collecting what would be written."""

    def __init__(self, existing: Files) -> None:
        self.existing = set(existing)
        self.written: Files = {}

    def write_bytes(self, location: str, path: str, data: bytes, *, replace: bool = False) -> None:
        assert location == LOCATION
        if path in self.existing and not replace:
            raise FileExistsError(path)
        self.written[path] = data


def _conforms() -> None:  # pragma: no cover - evaluated by mypy only
    _writer: PackWriter = MemoryPackWriter({})
