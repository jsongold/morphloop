"""Where drill items and answers live (#34, #61).

- pack items: ``PackV2.documents["drills"]``;
- generated items: ``GeneratedDocumentStore.list("drill")``
  (finalized and validated before they are stored, ADR-0014);
- answers: the ``drill.answers`` view, built from ``drill.answered`` events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from harness.core.drill.judge import stored_judgment
from harness.core.drill.model import ANSWERED, DrillItem
from harness.core.labels import HOLDOUT
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import EventTransactionV2, StoredEventV2, ViewDocumentStore
from harness.core.ports.generated_documents import GeneratedDocument
from harness.core.ports.json_types import JsonObject, to_plain_object
from harness.core.view import View


def _targets(checks: object) -> tuple[JsonObject, ...]:
    """``[{"check", "params"}]`` target conditions, or ``()`` if absent."""
    if not isinstance(checks, Sequence) or isinstance(checks, str | bytes):
        return ()
    return tuple(to_plain_object(cast(JsonObject, c)) for c in checks if isinstance(c, Mapping))


def _artifact_checks(specs: Iterable[Mapping[str, object]]) -> dict[str, tuple[JsonObject, ...]]:
    """``artifact id -> spec.checks``, the target conditions an item is judged by (#124)."""
    out: dict[str, tuple[JsonObject, ...]] = {}
    for spec in specs:
        body = spec.get("spec")
        if isinstance(body, Mapping):
            out[str(spec.get("id"))] = _targets(body.get("checks"))
    return out


def _required(doc: JsonObject, specs: dict[str, tuple[JsonObject, ...]]) -> tuple[JsonObject, ...]:
    return specs.get(str(doc.get("artifact_ref")), ())


def pack_items(pack: PackV2, *, include_holdout: bool = True) -> list[DrillItem]:
    """Every drill item bundled in ``pack``, in file path order.

    ``include_holdout=False`` drops ``sys:holdout`` items, for learner-facing
    practice surfaces (the notebook) that must not reveal held-out tasks.
    """
    required = _artifact_checks(pack.documents.get("artifacts", {}).values())
    drills = pack.documents.get("drills", {})
    items = [
        DrillItem.from_document(
            drills[path], origin="pack", required_checks=_required(drills[path], required)
        )
        for path in sorted(drills)
    ]
    return items if include_holdout else [item for item in items if HOLDOUT not in item.labels]


def generated_items(
    documents: Iterable[GeneratedDocument],
    artifacts: Iterable[GeneratedDocument] = (),
    *,
    pack: PackV2 | None = None,
) -> list[DrillItem]:
    """Generated items (``resource = 'drill'``, drill-item shaped bodies).

    ``artifacts`` are the generated artifact specs an ``artifact`` item may
    reference; the checks the generator validated them with (kept in provenance,
    not the body) become the item's target conditions (#124).
    ``pack`` supplies the pack artifacts a generated item still points at (the
    generator keeps the pack artifact when it makes no lab variant, #124 review),
    so those checks are required too.
    One labelled ``sys:holdout`` is never served: holdout is pack-only.
    """
    specs: list[Mapping[str, object]] = (
        [*pack.documents.get("artifacts", {}).values()] if pack is not None else []
    )
    required = _artifact_checks(specs)
    required |= {a.id: _targets(a.provenance.get("checks")) for a in artifacts}
    return [
        DrillItem.from_document(
            doc.body, origin="generated", required_checks=_required(doc.body, required)
        )
        for doc in documents
        if HOLDOUT not in (*doc.labels, *cast(Sequence[str], doc.body.get("labels", ())))
    ]


def learner_items_page(
    items: Iterable[DrillItem], *, after: str | None, limit: int
) -> tuple[list[DrillItem], str | None]:
    """One page of learner-listable ``items`` by id, plus the next page's
    ``after`` id (``None`` on the last page, #178).

    ``sys:holdout`` items are never listed: a learner must not see a held-out
    task ahead of time. They stay reachable by id for the transfer check.
    ponytail: sorts the whole in-memory list per page; bounded by authored content.
    """
    ordered = sorted(
        (i for i in items if HOLDOUT not in i.labels and (after is None or i.id > after)),
        key=lambda i: i.id,
    )
    page = ordered[:limit]
    return page, (page[-1].id if len(ordered) > limit else None)


class DrillAnswersView(View):
    """Answers keyed ``<ws_id>/<position:012d>``: a ws's answers in creation order (#178)."""

    name = "drill.answers"
    handles = frozenset({ANSWERED})

    @staticmethod
    def key(ws_id: str, position: int) -> str:
        return f"{ws_id}/{position:012d}"

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        assert event.ws_id is not None
        doc: dict[str, object] = {
            "event_id": event.id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "ws_id": event.ws_id,
            **event.payload,
        }
        tx.put_view(cls.name, cls.key(event.ws_id, event.position), cast(JsonObject, doc))


def list_answers(
    tx: EventTransactionV2, ws_id: str, *, after: str | None = None, limit: int
) -> tuple[list[JsonObject], str | None]:
    """One page of the ws's answers, oldest first, plus the next cursor key
    (#178): ``item_id``, ``answer_event_id``, and ``judgment_status``/``gap``
    (issue #129). Never ``actual`` or ``expected``.

    ``judgment_status``/``gap`` come from the answer's ``drill.judged`` event
    (#65, #134 review), looked up per answer; an answer with none yet reads
    back ``judgment_status: "unjudged"``, ``gap: None``.
    """
    pairs, next_cursor = DrillAnswersView.list(tx, key_prefix=f"{ws_id}/", after=after, limit=limit)
    answers: list[JsonObject] = []
    for _, doc in pairs:
        event_id = str(doc["event_id"])
        judgment = stored_judgment(tx, event_id)
        answers.append(
            {
                "item_id": doc["item_id"],
                "answer_event_id": event_id,
                "judgment_status": "judged" if judgment is not None else "unjudged",
                "gap": judgment.payload["gap"] if judgment is not None else None,
            }
        )
    return answers, next_cursor
