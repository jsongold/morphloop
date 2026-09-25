"""Drill item rules the schema cannot express (#61).

The schema already requires ``choices`` exactly for ``choice`` and
``artifact_ref`` exactly for ``artifact``. On top of that:

- item ids are unique across the pack;
- a ``choice`` item's ``expected`` is one of its ``choices``;
- ``artifact_ref`` names an artifact spec of the pack.

``sys:holdout`` is allowed on every item here because every item here is
pack-bundled; generated items carrying it are refused on read.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


def validate(pack: PackV2) -> Iterable[str]:
    drills = pack.documents.get("drills", {})
    artifact_ids = {str(doc["id"]) for doc in pack.documents.get("artifacts", {}).values()}
    ids = Counter(str(doc["id"]) for doc in drills.values())
    problems = [f"drill id {i!r} appears {n} times" for i, n in sorted(ids.items()) if n > 1]
    for path, doc in sorted(drills.items()):
        choices = doc.get("choices")
        if isinstance(choices, Sequence) and doc["expected"] not in choices:
            problems.append(f"{path}: expected is not one of the choices")
        ref = doc.get("artifact_ref")
        if ref is not None and ref not in artifact_ids:
            problems.append(f"{path}: artifact_ref {ref!r} is not an artifact of the pack")
    return problems
