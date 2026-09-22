"""The ``dns`` domain adapter object (``harness.core.domain_adapter.DomainAdapter``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from domains.dns.checks import (
    CommandExitCheck,
    HttpStatusCheck,
    NameResolvesCheck,
    ResolverAnswersCheck,
    ResolverConfigCheck,
)
from domains.dns.fixtures import ResolverLabFixture
from domains.dns.terminal import BashTerminalTool
from harness.core.domain_adapter import Check, DomainAdapter, FixtureProvider, TerminalTool

ADAPTER_ID = "dns"
VERSION = "0.1.0"
"""SemVer 2.0.0. Bump MINOR for new items/params, MAJOR for incompatible changes."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DnsDomainAdapter:
    adapter_id: str
    version: str
    fixtures: Mapping[str, FixtureProvider]
    checks: Mapping[str, Check]
    tools: Mapping[str, TerminalTool]


ADAPTER: DomainAdapter = DnsDomainAdapter(
    adapter_id=ADAPTER_ID,
    version=VERSION,
    fixtures=MappingProxyType({"resolver_lab": ResolverLabFixture()}),
    checks=MappingProxyType(
        {
            "name_resolves": NameResolvesCheck(),
            "resolver_answers": ResolverAnswersCheck(),
            "resolver_config": ResolverConfigCheck(),
            "http_status": HttpStatusCheck(),
            "command_exit": CommandExitCheck(),
        }
    ),
    tools=MappingProxyType({"terminal": BashTerminalTool()}),
)
