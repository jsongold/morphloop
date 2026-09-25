"""Notebook search over pack and generated resources."""

from collections.abc import Sequence
from pathlib import Path

from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.contract_schemas import ContractSchemas
from harness.core.notebook.search import search
from harness.core.pack.v2 import import_pack_v2
from harness.core.ports.generated_documents import GeneratedDocument
from harness.testing.fakes_v2 import InMemoryEventStoreV2
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = Path(__file__).parents[2] / "contracts/fixtures/pack-v2/valid/dns-pack"


class CountingDocuments(InMemoryGeneratedDocumentStore):
    def __init__(self) -> None:
        super().__init__()
        self.textbook_lists = 0
        self.textbook_gets = 0

    def list(self, resource: str, *, label: str | None = None) -> Sequence[GeneratedDocument]:
        if resource == "textbook":
            self.textbook_lists += 1
        return super().list(resource, label=label)

    def get(self, resource: str, id: str) -> GeneratedDocument | None:
        if resource == "textbook":
            self.textbook_gets += 1
        return super().get(resource, id)


def test_search_excludes_generated_content_from_another_pack() -> None:
    """#117 P1: search does not surface generated content of another pack revision."""
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    other_pack = {"pack_id": "other-pack", "pack_hash": "other-hash"}
    generated.add(
        GeneratedDocument(
            resource="textbook",
            id="old-doc",
            body={
                "id": "old-doc",
                "title": "Quarantined lesson",
                "labels": ["concept"],
                "blocks": [{"id": "b1", "body": "quarantined text", "labels": []}],
            },
            provenance=other_pack,
        )
    )
    generated.add(
        GeneratedDocument(
            resource="drill",
            id="old-drill",
            body={
                "id": "old-drill",
                "question": "Quarantined question",
                "expected": "x",
                "answer_mode": "text",
                "labels": ["concept"],
            },
            provenance=other_pack,
        )
    )
    with InMemoryEventStoreV2(ContractSchemas.load()).transaction() as tx:
        found = search("quarantined", pack=pack, generated=generated, tx=tx, user_id="usr_local")
    assert found == []


def test_search_excludes_pack_holdout_drills() -> None:
    """#117 P1: search must not expose a pack drill marked ``sys:holdout``."""
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = InMemoryGeneratedDocumentStore()
    with InMemoryEventStoreV2(ContractSchemas.load()).transaction() as tx:
        found = search("api.internal", pack=pack, generated=generated, tx=tx, user_id="usr_local")
    assert found == []


def test_generated_textbook_block_is_searchable_without_answer_leakage() -> None:
    pack = import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)
    generated = CountingDocuments()
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
    assert generated.textbook_lists == 2  # one corpus read per search
    assert generated.textbook_gets == 0  # no per-document fetches
