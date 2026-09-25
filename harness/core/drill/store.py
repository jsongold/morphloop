"""Where drill items and answers live (#34, #61).

- pack items: ``PackV2.documents["drills"]``;
- generated items: ``GeneratedDocumentStore.list("drill")``
  (finalized and validated before they are stored, ADR-0014);
- answers: the ``drill.answers`` view, built from ``drill.answered`` events.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import cast

from harness.core.drill.model import ANSWERED, DrillItem
from harness.core.labels import HOLDOUT
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.ports.json_types import JsonObject
from harness.core.view import View


def pack_items(pack: PackV2) -> list[DrillItem]:
    """Every drill item bundled in ``pack``, in file path order."""
    drills = pack.documents.get("drills", {})
    return [DrillItem.from_document(drills[path], origin="pack") for path in sorted(drills)]


def generated_items(documents: Iterable[GeneratedDocument]) -> list[DrillItem]:
    """Generated items (``resource = 'drill'``, drill-item shaped bodies).

    One labelled ``sys:holdout`` is never served: holdout is pack-only.
    """
    return [
        DrillItem.from_document(doc.body, origin="generated")
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


def list_answers(tx: ViewDocumentStore, ws_id: str) -> list[JsonObject]:
    """The ws's answers, in creation order: ``item_id``, ``answer_event_id``,
    and ``judgment_status``/``gap`` (issue #129). Never ``actual`` or
    ``expected``.

    Keys sort ``<item_id>`` before ``<position>`` (``DrillAnswersView.key``),
    so listing by ``key_prefix`` alone would group by item, not creation
    order -- the trailing zero-padded position is parsed back out to sort
    globally instead.

    No ``drill.judged`` view exists yet (gap-judging, issue #65, unmerged):
    every answer reads back ``judgment_status: "unjudged"``, ``gap: None``.
    Wiring that view in only changes the per-answer lookup below.
    """
    pairs = DrillAnswersView.list(tx, key_prefix=f"{ws_id}/")
    ordered = sorted(pairs, key=lambda pair: int(pair[0].rsplit("/", 1)[-1]))
    return [
        {
            "item_id": doc["item_id"],
            "answer_event_id": doc["event_id"],
            "judgment_status": "unjudged",
            "gap": None,
        }
        for _, doc in ordered
    ]
