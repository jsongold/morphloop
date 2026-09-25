"""Event v2 Port value types and the in-memory store's injectable clock.

The shared store contract suite is ``tests/adapters/postgres/test_event_store_v2.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.core.contract_schemas import EVENT_V2_APPEND_ID, EVENT_V2_STORED_ID
from harness.core.ports.events_v2 import EventV2, StoredEventV2
from harness.testing.fakes_v2 import (
    PROBE_EVENT_TYPE,
    InMemoryEventStoreV2,
    contract_schemas_with_probe,
)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
EVENT = EventV2(
    id="0190f5a2-7c3e-7d4b-8a1f-2b3c4d5e6f70",
    type=PROBE_EVENT_TYPE,
    actor="assistant",
    user_id="usr_01",
    payload={"note": "hi"},
)


def test_to_dict_omits_absent_scopes_and_is_contract_valid(tmp_path: Path) -> None:
    schemas = contract_schemas_with_probe(tmp_path)
    assert "session_id" not in EVENT.to_dict() and "ws_id" not in EVENT.to_dict()
    schemas.validate(EVENT.to_dict(), EVENT_V2_APPEND_ID)

    store = InMemoryEventStoreV2(schemas, now=lambda: T0)
    with store.transaction() as tx:
        stored = tx.append(EVENT).event
    assert stored.created_at == T0 and stored.position == 1
    assert stored.to_dict()["created_at"] == "2026-09-25T12:00:00Z"
    schemas.validate(stored.to_dict(), EVENT_V2_STORED_ID)
    assert EVENT.same_content_as(stored)


def test_stored_event_invariants() -> None:
    with pytest.raises(ValueError, match="position"):
        StoredEventV2(**_fields(), position=0, created_at=T0)
    with pytest.raises(ValueError, match="timezone"):
        StoredEventV2(**_fields(), position=1, created_at=datetime(2026, 9, 25))


def _fields() -> dict[str, object]:
    return {
        "id": EVENT.id,
        "type": EVENT.type,
        "actor": EVENT.actor,
        "user_id": EVENT.user_id,
        "payload": EVENT.payload,
    }


def test_connection_tracking_store_flags_a_second_connection(tmp_path: Path) -> None:
    from harness.testing.fakes_v2 import ConnectionTrackingStore

    store = ConnectionTrackingStore(InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path)))
    assert store.read() == []
    with store.transaction():
        with pytest.raises(AssertionError, match="read"):
            store.read()
        with pytest.raises(AssertionError, match="nest"):
            with store.transaction():
                pass
    assert store.read() == []  # tracking is reset once the transaction ends
