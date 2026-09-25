"""Textbook reads: reading order, base before generated, block plaintext (#55)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.core.pack.v2 import import_pack_v2
from harness.core.ports import JsonObject
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.textbook.service import Textbook, TextbookNotFoundError
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"
TOPIC = "network.dns.lookup-path"
GENERATED: JsonObject = {
    "id": "gen-lookup-path-001",
    "title": "More on lookups",
    "labels": ["troubleshooting"],
    "blocks": [{"id": "b1", "body": "Use **getent**.", "labels": []}],
}
SHADOW: JsonObject = {**GENERATED, "id": TOPIC, "title": "Shadow"}
# Store key "gen-keyed" differs from the body id; it also tries to spoof its origin.
KEYED: JsonObject = {**GENERATED, "id": "body-id", "labels": ["origin:pack"]}


@pytest.fixture(scope="module")
def textbook() -> Textbook:
    store = InMemoryGeneratedDocumentStore()
    for key, body in ((GENERATED["id"], GENERATED), (TOPIC, SHADOW), ("gen-keyed", KEYED)):
        store.add(
            GeneratedDocument(
                resource="textbook",
                id=str(key),
                body=body,
                labels=(f"topic:{TOPIC}",),
                provenance={},
            )
        )
    return Textbook(import_pack_v2(SE_PACK), store)


def test_reading_list_is_topic_docs_then_generated(textbook: Textbook) -> None:
    docs = textbook.reading_list(TOPIC)
    assert [d["id"] for d in docs] == [TOPIC, "gen-keyed", "gen-lookup-path-001"]
    assert "origin:pack" in docs[0]["labels"]  # type: ignore[operator]
    assert "origin:generated" in docs[2]["labels"]  # type: ignore[operator]
    assert set(docs[0]) == {"id", "title", "labels"}


def test_topic_without_docs_and_unknown_topic(textbook: Textbook) -> None:
    assert textbook.reading_list("network") == []
    with pytest.raises(TextbookNotFoundError):
        textbook.reading_list("nope")


def test_doc_has_block_plaintext(textbook: Textbook) -> None:
    doc = textbook.doc(TOPIC)
    assert doc["title"] == "How an application looks up a name"  # pack wins over SHADOW
    blocks = {b["id"]: b for b in doc["blocks"]}  # type: ignore[index, union-attr]
    assert blocks["diagram"]["plaintext"] == ""
    assert blocks["summary"]["plaintext"].startswith(
        "Applications do not speak DNS. They call getaddrinfo(),"
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
