"""Every item, block and artifact label is valid (:mod:`harness.core.labels`)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from harness.core.labels import label_problems

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

_UNLABELLED = frozenset({"labels", "topics"})


def _labels(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, Sequence) else []


def _labelled(pack: PackV2) -> Iterator[tuple[str, list[str]]]:
    """``(where, labels)`` for each document and each of its blocks."""
    for kind, docs in pack.documents.items():
        if kind in _UNLABELLED:
            continue
        for path, doc in docs.items():
            yield path, _labels(doc.get("labels"))
            blocks = doc.get("blocks", ())
            assert isinstance(blocks, Sequence)
            for block in blocks:
                if isinstance(block, Mapping):
                    yield f"{path}#{block.get('id')}", _labels(block.get("labels"))


def validate(pack: PackV2) -> Iterable[str]:
    topic_ids = frozenset(pack.topic_ids)
    return [
        f"{where}: label {label!r} {why}"
        for where, labels in _labelled(pack)
        for label, why in sorted(
            label_problems(labels, vocabulary=pack.labels, topic_ids=topic_ids).items()
        )
    ]
