"""Textbook reads: reading order, base before generated, block plaintext (#55)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.pack.v2 import import_pack_v2
from harness.core.ports import JsonObject
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.textbook.service import Textbook, TextbookNotFoundError
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)
TOPIC = "network.dns.resolution"
DOC = "dns-resolution"  # the one pack doc under TOPIC
GENERATED: JsonObject = {
    "id": "gen-lookup-path-001",
    "title": "More on lookups",
    "labels": ["troubleshooting"],
    "blocks": [{"id": "b1", "body": "Use **getent**.", "labels": []}],
}
SHADOW: JsonObject = {**GENERATED, "id": DOC, "title": "Shadow"}
# Store key "gen-keyed" differs from the body id; it also tries to spoof its origin.
KEYED: JsonObject = {**GENERATED, "id": "body-id", "labels": ["origin:pack"]}


@pytest.fixture(scope="module")
def textbook() -> Textbook:
    store = InMemoryGeneratedDocumentStore()
    for key, body in ((GENERATED["id"], GENERATED), (DOC, SHADOW), ("gen-keyed", KEYED)):
        store.add(
            GeneratedDocument(
                resource="textbook",
                id=str(key),
                body=body,
                labels=(f"topic:{TOPIC}",),
                provenance={},
            )
        )
    return Textbook(import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES), store)


def test_reading_list_is_topic_docs_then_generated(textbook: Textbook) -> None:
    docs = textbook.reading_list(TOPIC)
    assert [d["id"] for d in docs] == [DOC, "gen-keyed", "gen-lookup-path-001"]
    assert "origin:pack" in docs[0]["labels"]  # type: ignore[operator]
    assert "origin:generated" in docs[2]["labels"]  # type: ignore[operator]
    assert set(docs[0]) == {"id", "title", "labels"}


def test_topic_without_docs_and_unknown_topic(textbook: Textbook) -> None:
    assert textbook.reading_list("network") == []
    with pytest.raises(TextbookNotFoundError):
        textbook.reading_list("nope")


def test_doc_has_block_plaintext(textbook: Textbook) -> None:
    doc = textbook.doc(DOC)
    assert doc["title"] == "How a name is resolved"  # pack wins over SHADOW
    blocks = {b["id"]: b for b in doc["blocks"]}  # type: ignore[index, union-attr]
    assert blocks["b2"]["plaintext"] == ""  # an artifact directive has no text
    assert blocks["b1"]["plaintext"] == (
        "A stub resolver reads /etc/resolv.conf and asks the listed nameserver."
    )


def test_generated_doc_and_unknown_doc(textbook: Textbook) -> None:
    doc = textbook.doc("gen-lookup-path-001")
    assert doc["labels"] == ["troubleshooting", f"topic:{TOPIC}", "origin:generated"]
    assert doc["blocks"][0]["plaintext"] == "Use getent."  # type: ignore[index, call-overload]
    with pytest.raises(TextbookNotFoundError):
        textbook.doc("nope")


def test_generated_doc_id_is_store_key_and_origin_is_not_spoofable(textbook: Textbook) -> None:
    listed = next(d for d in textbook.reading_list(TOPIC) if d["id"] == "gen-keyed")
    doc = textbook.doc("gen-keyed")
    assert doc["id"] == "gen-keyed"
    for labels in (listed["labels"], doc["labels"]):
        assert [x for x in labels if x.startswith("origin:")] == ["origin:generated"]  # type: ignore[union-attr]
