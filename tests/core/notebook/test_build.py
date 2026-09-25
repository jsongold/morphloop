"""Notebook workspace composition on the event transaction."""

import uuid
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.notebook.build import build_workspace
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.session.service import create_session
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def test_build_selects_generated_docs_and_checks_session_owner() -> None:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    generated.add(
        GeneratedDocument(
            resource="textbook",
            id="generated-doc",
            body={
                "id": "generated-doc",
                "title": "Generated lesson",
                "labels": ["concept"],
                "blocks": [{"id": "b1", "body": "Generated text", "labels": []}],
            },
            provenance={},
        )
    )
    store = InMemoryEventStoreV2(ContractSchemas.load())
    with store.transaction() as tx:
        session = create_session(
            tx,
            pack=pack,
            user_id="usr_local",
            event_id=str(uuid.uuid4()),
            pack_id=pack.pack_id,
            topic_id="network.dns",
        )
        params = {
            "pack": pack,
            "generated": generated,
            "event_id": str(uuid.uuid4()),
            "session_id": str(session["id"]),
            "labels": ["concept"],
        }
        result = build_workspace(tx, user_id="usr_local", **params)
        assert any(doc["id"] == "generated-doc" for doc in result["documents"])
        with pytest.raises(LookupError):
            build_workspace(tx, user_id="usr_other", **{**params, "event_id": str(uuid.uuid4())})
