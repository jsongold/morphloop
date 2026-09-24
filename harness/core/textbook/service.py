"""Textbook reads (#34, #55): a topic's reading list and one doc with block plaintext.

Docs come from two places, told apart by an ``origin:`` label added here:

- ``origin:pack``: the pack's textbook docs, in ``topic.docs[]`` order (the base text);
- ``origin:generated``: runtime-generated docs (:class:`GeneratedDocumentStore`,
  resource ``textbook``, body shaped like ``pack/v2/textbook-doc.json``), attached
  to a topic by the row label ``topic:<id>``, listed after the base text.

A generated doc never shadows a pack doc with the same id.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from harness.core.labels import TOPIC_PREFIX
from harness.core.pack.v2 import PackV2
from harness.core.ports import JsonObject, PlainJson
from harness.core.ports.generated_documents import GeneratedDocument, GeneratedDocumentStore
from harness.core.ports.json_types import to_plain_object
from harness.core.textbook.plaintext import block_plaintext

ORIGIN_PACK = "origin:pack"
ORIGIN_GENERATED = "origin:generated"

RESOURCE = "textbook"


class TextbookNotFoundError(LookupError):
    """No such topic or doc."""


def _walk(topics: Iterable[JsonObject]) -> Iterator[JsonObject]:
    for topic in topics:
        yield topic
        children = topic.get("topics", ())
        assert isinstance(children, Sequence)
        yield from _walk(c for c in children if isinstance(c, Mapping))


def _with_origin(doc: JsonObject, origin: str) -> dict[str, PlainJson]:
    out = to_plain_object(doc)
    labels = out.get("labels")
    assert isinstance(labels, list)
    if origin not in labels:
        labels.append(origin)
    return out


def _generated(doc: GeneratedDocument) -> dict[str, PlainJson]:
    """The body with the row labels merged in, plus ``origin:generated``."""
    labels = doc.body.get("labels", ())
    assert isinstance(labels, Sequence)
    merged = [*labels, *(label for label in doc.labels if label not in labels)]
    return _with_origin({**doc.body, "labels": merged}, ORIGIN_GENERATED)


def _summary(doc: dict[str, PlainJson]) -> dict[str, PlainJson]:
    return {k: doc[k] for k in ("id", "title", "labels")}


@dataclass(frozen=True, slots=True)
class Textbook:
    pack: PackV2
    generated: GeneratedDocumentStore

    def _pack_docs(self) -> dict[str, JsonObject]:
        return {str(d["id"]): d for d in self.pack.documents["textbooks"].values()}

    def reading_list(self, topic_id: str) -> list[dict[str, PlainJson]]:
        """Summaries (id, title, labels) of a topic's docs: base first, then generated."""
        topic = next((t for t in _walk(self.pack.topics) if t["id"] == topic_id), None)
        if topic is None:
            raise TextbookNotFoundError(f"no topic {topic_id!r}")
        pack_docs = self._pack_docs()
        doc_ids = topic.get("docs", ())
        assert isinstance(doc_ids, Sequence)
        docs = [_with_origin(pack_docs[str(i)], ORIGIN_PACK) for i in doc_ids]
        for doc in self.generated.list(RESOURCE, label=TOPIC_PREFIX + topic_id):
            if doc.id not in pack_docs:
                docs.append(_generated(doc))
        return [_summary(d) for d in docs]

    def doc(self, doc_id: str) -> dict[str, PlainJson]:
        """One doc; every block gets its ``plaintext`` (:mod:`.plaintext`)."""
        found = self._pack_docs().get(doc_id)
        if found is not None:
            out = _with_origin(found, ORIGIN_PACK)
        else:
            generated = self.generated.get(RESOURCE, doc_id)
            if generated is None:
                raise TextbookNotFoundError(f"no textbook doc {doc_id!r}")
            out = _generated(generated)
        blocks = out["blocks"]
        assert isinstance(blocks, list)
        for block in blocks:
            assert isinstance(block, dict)
            block["plaintext"] = block_plaintext(str(block["body"]))
        return out
