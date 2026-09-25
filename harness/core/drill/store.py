"""Where drill items and answers live (#34, #61).

- pack items: ``PackV2.documents["drills"]``;
- generated items: ``GeneratedDocumentStore.list("drill")``
  (finalized and validated before they are stored, ADR-0014);
- answers: the ``drill.answers`` view, built from ``drill.answered`` events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from harness.core.drill.model import ANSWERED, DrillItem
from harness.core.labels import HOLDOUT
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.ports.json_types import JsonObject
from harness.core.view import View


def _artifact_checks(specs: Iterable[Mapping[str, object]]) -> dict[str, tuple[str, ...]]:
    """``artifact id -> spec.allowed_checks``, the checks an item must have run (#124)."""
    out: dict[str, tuple[str, ...]] = {}
    for spec in specs:
        body = spec.get("spec")
        if not isinstance(body, Mapping):
            continue
        checks = body.get("allowed_checks")
        if isinstance(checks, Sequence) and not isinstance(checks, str | bytes):
            out[str(spec.get("id"))] = tuple(str(check) for check in checks)
    return out


def _required(doc: JsonObject, specs: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return specs.get(str(doc.get("artifact_ref")), ())


def pack_items(pack: PackV2) -> list[DrillItem]:
    """Every drill item bundled in ``pack``, in file path order."""
    required = _artifact_checks(pack.documents.get("artifacts", {}).values())
    drills = pack.documents.get("drills", {})
    return [
        DrillItem.from_document(
            drills[path], origin="pack", required_checks=_required(drills[path], required)
        )
        for path in sorted(drills)
    ]


def generated_items(
    documents: Iterable[GeneratedDocument], artifacts: Iterable[GeneratedDocument] = ()
) -> list[DrillItem]:
    """Generated items (``resource = 'drill'``, drill-item shaped bodies).

    ``artifacts`` are the generated artifact specs an ``artifact`` item may
    reference; their ``allowed_checks`` become the item's required checks (#124).
    One labelled ``sys:holdout`` is never served: holdout is pack-only.
    """
    required = _artifact_checks(artifact.body for artifact in artifacts)
    return [
        DrillItem.from_document(
            doc.body, origin="generated", required_checks=_required(doc.body, required)
        )
        for doc in documents
        if HOLDOUT not in (*doc.labels, *cast(Sequence[str], doc.body.get("labels", ())))
    ]


class DrillAnswersView(View):
    """Answers keyed ``<ws_id>/<item_id>/<position>``: list a ws or one item with a prefix."""

    name = "drill.answers"
    handles = frozenset({ANSWERED})

    @staticmethod
    def key(ws_id: str, item_id: str, position: int) -> str:
        return f"{ws_id}/{item_id}/{position:012d}"

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None
        item_id = str(event.payload["item_id"])
        doc: dict[str, object] = {
            "event_id": event.id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "ws_id": event.ws_id,
            **event.payload,
        }
        tx.put_view(cls.name, cls.key(event.ws_id, item_id, event.position), cast(JsonObject, doc))
