"""Notebook search over pack and generated resources."""

from pathlib import Path

from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.notebook.search import search
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


def test_generated_textbook_block_is_searchable_without_answer_leakage() -> None:
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
                "blocks": [{"id": "b1", "body": "Use **getent** to inspect DNS.", "labels": []}],
            },
            provenance={},
        )
    )
    generated.add(
        GeneratedDocument(
            resource="drill",
            id="generated-drill",
            body={
                "id": "generated-drill",
                "question": "Explain DNS",
                "expected": "hidden-answer",
                "answer_mode": "text",
                "labels": ["concept"],
            },
            provenance={},
        )
    )
    with InMemoryEventStoreV2(ContractSchemas.load()).transaction() as tx:
        found = search("GETENT", pack=pack, generated=generated, tx=tx, user_id="usr_local")
        secret = search("hidden-answer", pack=pack, generated=generated, tx=tx, user_id="usr_local")
    assert any(r["kind"] == "textbook_block" and r["id"] == "generated-doc" for r in found)
    assert secret == []
