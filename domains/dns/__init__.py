"""DNS domain adapter (``adapter_id`` ``"dns"``; ADR-0009).

Wiring obtains it with :func:`adapter` (or the :data:`ADAPTER` constant) and
registers it::

    import domains.dns
    registry.register(domains.dns.adapter())

Registered items (pack ids ``dns.<name>``):

- fixture ``resolver_lab`` (:mod:`domains.dns.fixtures`)
- checks ``name_resolves``, ``resolver_answers``, ``resolver_config``,
  ``http_status``, ``command_exit`` (:mod:`domains.dns.checks`)
- tool ``terminal`` (:mod:`domains.dns.terminal`)

This package imports ``harness.core`` only (``.importlinter``).
"""

from __future__ import annotations

from domains.dns.adapter import ADAPTER, ADAPTER_ID, VERSION
from harness.core.domain_adapter import DomainAdapter

__all__ = ["ADAPTER", "ADAPTER_ID", "VERSION", "adapter"]


def adapter() -> DomainAdapter:
    """The DNS domain adapter, ready to register."""
    return ADAPTER
