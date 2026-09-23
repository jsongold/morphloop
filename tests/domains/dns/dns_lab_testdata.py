"""Shared test data for tests/domains/dns (imported as a top-level module)."""

from __future__ import annotations

from typing import Any

from harness.core.ports import ImageRef

IMAGE = ImageRef(
    repository="docker.io/nicolaka/netshoot",
    digest="sha256:7f08c4aff13ff61a35d30e30c5c1ea8396eac6ab4ce19fd02d5a4b3b5d0d09a2",
)

# Explicit form of the lab in
# contents/software-engineering/environments/gen-diagnose-dns-resolver-misconfiguration-001.json.
PARAMS: dict[str, Any] = {
    "network": "none",
    "zone_server": {
        "listen_address": "127.0.0.53",
        "port": 53,
        "ttl_seconds": 60,
        "records": [
            {"name": "api.corp.internal", "type": "A", "address": "127.0.10.20"},
            {"name": "metrics.corp.internal", "type": "A", "address": "127.0.10.30"},
        ],
    },
    "services": [
        {
            "name": "api.corp.internal",
            "listen_address": "127.0.10.20",
            "port": 8080,
            "health_path": "/health",
            "health_body": "ok",
        }
    ],
    "resolv_conf": {
        "nameservers": ["192.0.2.53"],
        "search": [],
        "options": ["timeout:1", "attempts:1"],
    },
}

# Fault form, as the contents/software-engineering environment
# gen-diagnose-dns-resolver-misconfiguration-001.json declares it; translates to
# PARAMS except for the health body.
FAULT_PARAMS: dict[str, Any] = {
    "zone": "corp.internal",
    "service_name": "api.corp.internal",
    "service_address": "127.0.10.20",
    "service_port": 8080,
    "health_path": "/health",
    "dns_server_address": "127.0.0.53",
    "record_ttl_seconds": 60,
    "fault": "wrong_nameserver",
    "configured_nameserver": "192.0.2.53",
    "resolver_timeout_seconds": 1,
    "resolver_attempts": 1,
}
