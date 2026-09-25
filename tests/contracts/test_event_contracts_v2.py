"""Contract tests for the v2 event envelope (#43).

The v2 dispatch is assembled by ``harness.core.contract_schemas`` from payload
files declaring ``"x-envelope": 2``, so these tests validate through
``ContractSchemas``. Auto-discovery is proven on a temp copy of ``contracts/``
with a test-only payload type added; real v2 types belong to resource PRs,
so checks against the real set are subset checks.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from harness.core.contract_schemas import (
    EVENT_V2_APPEND_ID,
    EVENT_V2_STORED_ID,
    ContractSchemas,
    ContractValidationError,
)
from harness.core.ports.events_v2 import EventV2
from harness.testing.contracts import CONTRACTS_DIR
from harness.testing.fakes_v2 import InMemoryEventStoreV2, contract_schemas_with_probe

PAYLOADS = Path("schemas/events/payloads")
PROBE_TYPE = "probe.created"
ACTORS = json.loads((CONTRACTS_DIR / "schemas/events/envelope/v2/fields.json").read_text())[
    "properties"
]["actor"]["enum"]

EVENT: dict[str, Any] = {
    "id": "0190f5a2-7c3e-7d4b-8a1f-2b3c4d5e6f70",
    "type": PROBE_TYPE,
    "actor": "learner",
    "user_id": "usr_01",
    "session_id": "ses_01",
    "ws_id": "ws_01",
    "payload": {"note": "hi"},
}
STORED = {**EVENT, "position": 1, "created_at": "2026-09-24T12:00:00Z"}


def _payload(n: int, actors: list[str]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://morphloop.dev/contracts/{PAYLOADS}/{PROBE_TYPE}/{n}.json",
        "x-envelope": 2,
        "x-actors": actors,
        "type": "object",
        "required": ["note"],
        "properties": {"note": {"type": "string"}},
        "additionalProperties": False,
    }


@pytest.fixture(scope="module")
def schemas(tmp_path_factory: pytest.TempPathFactory) -> ContractSchemas:
    contracts = tmp_path_factory.mktemp("v2") / "contracts"
    shutil.copytree(CONTRACTS_DIR, contracts)
    for n, actors in ((1, ["learner", "assistant"]), (2, ["system"])):
        path = contracts / PAYLOADS / PROBE_TYPE / f"{n}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_payload(n, actors)), encoding="utf-8")
    return ContractSchemas(contracts)


def test_real_contracts_load_and_declare_known_actors() -> None:
    real = ContractSchemas(CONTRACTS_DIR)
    for payload_id in real.event_types_v2.values():
        actors = json.loads((CONTRACTS_DIR / payload_id.split("contracts/", 1)[1]).read_text())
        assert set(actors["x-actors"]) <= set(ACTORS), payload_id


def test_payload_file_is_discovered(schemas: ContractSchemas) -> None:
    base = f"https://morphloop.dev/contracts/{PAYLOADS}/{PROBE_TYPE}"
    # Subset: real v2 types added by resource PRs are discovered alongside the probe.
    probe = {k: v for k, v in schemas.event_types_v2.items() if k.startswith(f"{PROBE_TYPE}")}
    assert probe == {
        PROBE_TYPE: f"{base}/1.json",
        f"{PROBE_TYPE}.v2": f"{base}/2.json",
    }
    real = ContractSchemas(CONTRACTS_DIR).event_types_v2
    assert schemas.event_types_v2 == {**real, **probe}


def test_valid_events(schemas: ContractSchemas) -> None:
    schemas.validate(EVENT, EVENT_V2_APPEND_ID)
    schemas.validate(STORED, EVENT_V2_STORED_ID)
    minimal = {k: v for k, v in EVENT.items() if k not in ("session_id", "ws_id")}
    schemas.validate(minimal, EVENT_V2_APPEND_ID)
    schemas.validate({**EVENT, "type": f"{PROBE_TYPE}.v2", "actor": "system"}, EVENT_V2_APPEND_ID)


def _with(path: tuple[str, ...], value: Any, event: dict[str, Any] = EVENT) -> dict[str, Any]:
    out = copy.deepcopy(event)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


@pytest.mark.parametrize(
    ("instance", "schema_id", "error_path"),
    [
        (_with(("type",), "probe.deleted"), EVENT_V2_APPEND_ID, "$.type"),
        (_with(("type",), "probe.created.v3"), EVENT_V2_APPEND_ID, "$.type"),
        (_with(("actor",), "system"), EVENT_V2_APPEND_ID, "$.actor"),
        (_with(("actor",), "tutor"), EVENT_V2_APPEND_ID, "$.actor"),
        (_with(("event_version",), 1), EVENT_V2_APPEND_ID, "$"),
        (_with(("payload", "extra"), 1), EVENT_V2_APPEND_ID, "$.payload"),
        (_with(("id",), "evt_01"), EVENT_V2_APPEND_ID, "$.id"),
        (STORED, EVENT_V2_APPEND_ID, "$"),
        (EVENT, EVENT_V2_STORED_ID, "$"),
    ],
    ids=[
        "unknown-type",
        "unknown-version",
        "undeclared-actor",
        "unknown-actor",
        "extra-envelope-field",
        "extra-payload-field",
        "id-not-uuid",
        "append-with-db-fields",
        "stored-without-db-fields",
    ],
)
def test_invalid_event_is_rejected(
    schemas: ContractSchemas, instance: dict[str, Any], schema_id: str, error_path: str
) -> None:
    errors = schemas.errors(instance, schema_id)
    assert error_path in {e.split(": ", 1)[0] for e in errors}, errors


def test_v1_events_unaffected(schemas: ContractSchemas) -> None:
    v1_stored = CONTRACTS_DIR.parent / "tests/contracts/fixtures/events/valid"
    for path in sorted(v1_stored.glob("*.json")):
        schemas.validate(
            json.loads(path.read_text()),
            ContractSchemas.id_for("schemas/events/envelope/stored.json"),
        )


@pytest.mark.parametrize(
    ("scope", "missing"),
    [(["session_id"], ("session_id",)), (["session_id", "ws_id"], ("ws_id",))],
)
def test_x_scope_requires_envelope_ids(
    tmp_path: Path, scope: list[str], missing: tuple[str, ...]
) -> None:
    scoped = contract_schemas_with_probe(tmp_path, scope=scope)
    scoped.validate(EVENT, EVENT_V2_APPEND_ID)
    event = {k: v for k, v in EVENT.items() if k not in missing}
    with pytest.raises(ContractValidationError):
        scoped.validate(event, EVENT_V2_APPEND_ID)
    store = InMemoryEventStoreV2(scoped)
    with pytest.raises(ContractValidationError), store.transaction() as tx:
        tx.append(EventV2(**event))  # type: ignore[arg-type]
    assert store.read() == []


def test_unscoped_type_accepts_missing_ids(schemas: ContractSchemas) -> None:
    event = {k: v for k, v in EVENT.items() if k not in ("session_id", "ws_id")}
    schemas.validate(event, EVENT_V2_APPEND_ID)


def test_invalid_x_scope_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="x-scope"):
        contract_schemas_with_probe(tmp_path, scope=["ws_id"])
