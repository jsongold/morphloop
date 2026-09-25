"""Artifact spec ids are unique across the pack.

Consumers look up an artifact spec by id (:func:`harness.core.artifact.learner_artifact_spec`,
``lab_specs``); a duplicate id would leave which one "wins" undefined.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


def validate(pack: PackV2) -> Iterable[str]:
    ids = Counter(str(doc["id"]) for doc in pack.documents.get("artifacts", {}).values())
    return [f"artifact id {i!r} appears {n} times" for i, n in sorted(ids.items()) if n > 1]
