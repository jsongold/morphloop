"""drill resource core (#61): items, answers, pack validator."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas, ContractValidationError
from harness.core.drill import (
    AnswerMismatchError,
    DrillAnswersView,
    DrillItemNotFoundError,
    DrillService,
    WsNotFoundError,
    generated_items,
    pack_items,
    ws_session_id,
)
from harness.core.pack.v2 import PackV2ImportError, import_pack_v2
from harness.core.ports.events_v2 import StoredEventV2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.ports.json_types import JsonObject
from harness.testing.fakes_v2 import InMemoryEventStoreV2

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)
GENERATED: dict[str, Any] = {
    "id": "0b6f9a3e-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
    "question": "What does `dig` print?",
    "expected": "The answer section.",
    "answer_mode": "text",
    "labels": ["topic:network.dns.records"],
}


def _generated(body: JsonObject, labels: tuple[str, ...] = ()) -> GeneratedDocument:
    return GeneratedDocument(
        resource="drill", id=str(body["id"]), body=body, labels=labels, provenance={}
    )


@pytest.fixture
def service() -> DrillService:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    holdout = {**GENERATED, "id": "gen-holdout", "labels": ["sys:holdout"]}
    column_holdout = _generated({**GENERATED, "id": "gen-holdout-2"}, ("sys:holdout",))
    generated = [_generated(GENERATED), _generated(holdout), column_holdout]
    return DrillService([*pack_items(pack), *generated_items(generated)])


def _answer(service: DrillService, store: InMemoryEventStoreV2, **kw: Any) -> Any:
    with store.transaction() as tx:
        return service.answer(
            tx, event_id=kw.pop("event_id", str(uuid.uuid4())), user_id="usr_1", ws_id="ws_1", **kw
        )


def test_pack_and_generated_items_are_peers_told_apart_by_label(service: DrillService) -> None:
    by_origin = {o: service.list_items([f"origin:{o}"]) for o in ("pack", "generated")}
    assert len(by_origin["pack"]) == 3  # pack holdout items are served
    assert [i.id for i in by_origin["generated"]] == [GENERATED["id"]]
    records = service.list_items(["topic:network.dns.records"])
    assert GENERATED["id"] in {i.id for i in records}
    assert not {"gen-holdout", "gen-holdout-2"} & {i.id for i in service.list_items()}


def test_learner_view_never_has_expected(service: DrillService) -> None:
    for item in service.list_items():
        assert "expected" not in item.for_learner()
    assert service.get_item("dns-record-choice").for_learner()["choices"]
    with pytest.raises(DrillItemNotFoundError):
        service.get_item("nope")


def test_answer_appends_event_and_view_once(service: DrillService) -> None:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    event_id = str(uuid.uuid4())
    for _ in range(2):
        event = _answer(service, store, event_id=event_id, item_id="dns-record-choice", actual="A")
    assert event.type == "drill.answered" and event.ws_id == "ws_1"
    assert event.payload == {
        "item_id": "dns-record-choice",
        "answer_mode": "choice",
        "actual": "A",
    }
    assert len(store.read(ws_id="ws_1")) == 1
    with store.transaction() as tx:
        docs = DrillAnswersView.list(tx, key_prefix="ws_1/dns-record-choice/")
    assert [d["actual"] for _, d in docs] == ["A"]


def test_answer_must_fit_the_answer_mode(service: DrillService) -> None:
    store = InMemoryEventStoreV2(ContractSchemas.load())
    lab = "dns-fix-resolver-lab"
    for kw in (
        {"item_id": "dns-record-choice", "actual": "FOO"},
        {"item_id": "dns-record-choice", "artifact_id": "art_1"},
        {"item_id": lab, "actual": "fixed it"},
    ):
        with pytest.raises(AnswerMismatchError):
            _answer(service, store, **kw)
    assert _answer(service, store, item_id=lab, artifact_id="art_1").payload["artifact_id"]
    with pytest.raises(ContractValidationError):
        _answer(service, store, item_id=lab, artifact_id="not-an-artifact-id")


def _edit(path: Path, **changes: Any) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_validator_checks_choices_and_artifact_ref(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    _edit(pack / "drills" / "dns-record-choice.json", expected="TXT")
    _edit(pack / "drills" / "dns-fix-resolver-lab.json", artifact_ref="no-such-lab")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
    problems = "\n".join(info.value.problems)
    assert "[drill] drills/dns-record-choice.json: expected is not one of the choices" in problems
    assert "artifact_ref 'no-such-lab' is not an artifact of the pack" in problems


def test_ws_session_comes_from_ws_created() -> None:
    created = StoredEventV2(
        id=str(uuid.uuid4()),
        type="ws.created",
        actor="learner",
        user_id="usr_1",
        session_id="ses_1",
        ws_id="ws_1",
        payload={},
        position=1,
        created_at=datetime.now(UTC),
    )
    assert ws_session_id("ws_1", [created]) == "ses_1"
    with pytest.raises(WsNotFoundError):
        ws_session_id("ws_2", [])
