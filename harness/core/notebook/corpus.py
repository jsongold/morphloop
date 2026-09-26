"""The learner-visible drill corpus shared by notebook build and search.

The notebook is a practice and discovery surface, unlike the drill route: it
never serves pack ``sys:holdout`` items, and it serves generated content only
when the document's provenance belongs to the pinned pack revision.
"""

from __future__ import annotations

from harness.core.drill.service import DrillService
from harness.core.drill.store import generated_items, pack_items
from harness.core.pack.v2 import PackV2
from harness.core.ports.generated_documents import GeneratedDocumentStore, belongs_to_pack


def learner_drills(pack: PackV2, generated: GeneratedDocumentStore) -> DrillService:
    """Pack items (holdout dropped) plus generated items of this pack revision."""
    documents = [
        doc
        for doc in generated.list("drill")
        if belongs_to_pack(doc, pack_id=pack.pack_id, pack_hash=pack.pack_hash)
    ]
    return DrillService([*pack_items(pack, include_holdout=False), *generated_items(documents)])
