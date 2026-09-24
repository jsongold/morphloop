"""Topic tree rules the schema cannot express.

- topic ids are unique across the pack;
- every ``topic.docs[]`` id is a textbook doc, and every textbook doc is listed
  under exactly one topic.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from harness.core.ports import JsonObject

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


def _topic_docs(topic: JsonObject) -> Iterator[str]:
    docs = topic.get("docs", ())
    children = topic.get("topics", ())
    assert isinstance(docs, Sequence) and isinstance(children, Sequence)
    yield from (str(d) for d in docs)
    for child in children:
        if isinstance(child, Mapping):
            yield from _topic_docs(child)


def validate(pack: PackV2) -> Iterable[str]:
    problems = [
        f"topic id {i!r} appears {n} times"
        for i, n in sorted(Counter(pack.topic_ids).items())
        if n > 1
    ]
    textbook_ids = {str(doc["id"]) for doc in pack.documents.get("textbooks", {}).values()}
    listed = Counter(d for topic in pack.topics for d in _topic_docs(topic))
    for doc_id in sorted(listed.keys() | textbook_ids):
        n = listed[doc_id]
        if doc_id not in textbook_ids:
            problems.append(f"topic docs: {doc_id!r} is not a textbook doc id")
        elif n == 0:
            problems.append(f"textbook doc {doc_id!r} is not listed under any topic")
        elif n > 1:
            problems.append(f"textbook doc {doc_id!r} is listed under {n} topics")
    return problems
