"""Id and clock suppliers for the learning loop (ADR-0016).

Clock and id generation are not Ports: core takes them as arguments. The
services in this package take one :data:`IdGenerator` and one clock callable so
tests can make both deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

type IdGenerator = Callable[[str], str]
"""``prefix -> '<prefix>_<opaque>'`` (``contracts/schemas/common/ids.json``)."""

type Clock = Callable[[], datetime]


def uuid_ids(prefix: str) -> str:
    """Random id of the form ``<prefix>_<32 hex>``."""
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    """Current time as an aware UTC ``datetime``."""
    return datetime.now(UTC)


def sequence_ids(counter: Callable[[], int]) -> IdGenerator:
    """Deterministic ids built from ``counter`` (tests)."""

    def generate(prefix: str) -> str:
        return f"{prefix}_{counter():d}"

    return generate
