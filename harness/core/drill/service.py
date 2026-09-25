"""Drill service: find items, record answers (#34, #61).

Recording an answer appends ``drill.answered`` and updates the views in the
same transaction (ADR-0008). Judging the gap is a separate step (#65).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence

from harness.core.drill.model import ANSWERED, DrillItem
from harness.core.ports.events_v2 import EventTransactionV2, EventV2, StoredEventV2
from harness.core.ports.json_types import PlainJson
from harness.core.view import dispatch


class DrillError(Exception):
    """Base class; ``status`` is the HTTP status the API maps it to."""

    status = 400


class DrillItemNotFoundError(DrillError):
    status = 404


class WsNotFoundError(DrillError):
    status = 404


class AnswerMismatchError(DrillError):
    """The answer does not fit the item's ``answer_mode`` (or is not one of its choices)."""


WS_CREATED = "ws.created"


def ws_session_id(ws_id: str, events: Sequence[StoredEventV2]) -> str | None:
    """The session of ``ws_id``, from its ``ws.created`` event (among the ws's ``events``).

    Raises :class:`WsNotFoundError` when the ws was never created.
    """
    for event in events:
        if event.type == WS_CREATED and event.ws_id == ws_id:
            return event.session_id
    raise WsNotFoundError(f"no ws {ws_id!r}")


class DrillService:
    """Pack and generated items side by side (told apart by their ``origin:`` label)."""

    def __init__(self, items: Iterable[DrillItem]) -> None:
        self._items: dict[str, DrillItem] = {}
        for item in items:
            self._items.setdefault(item.id, item)

    def list_items(self, labels: Collection[str] = ()) -> list[DrillItem]:
        """Items carrying every one of ``labels``."""
        wanted = set(labels)
        return [item for item in self._items.values() if wanted <= set(item.labels)]

    def get_item(self, item_id: str) -> DrillItem:
        item = self._items.get(item_id)
        if item is None:
            raise DrillItemNotFoundError(f"no drill item {item_id!r}")
        return item

    def answer(
        self,
        tx: EventTransactionV2,
        *,
        event_id: str,
        user_id: str,
        ws_id: str,
        item_id: str,
        session_id: str | None = None,
        actual: str | None = None,
        artifact_id: str | None = None,
    ) -> StoredEventV2:
        """Append ``drill.answered`` (idempotent on ``event_id``) and update views."""
        item = self.get_item(item_id)
        if item.answer_mode == "artifact":
            if artifact_id is None or actual is not None:
                raise AnswerMismatchError("an 'artifact' item is answered with artifact_id only")
        elif actual is None or artifact_id is not None:
            raise AnswerMismatchError(f"a {item.answer_mode!r} item is answered with actual only")
        if item.choices is not None and actual not in item.choices:
            raise AnswerMismatchError("actual is not one of the item's choices")
        payload: dict[str, PlainJson] = {"item_id": item.id, "answer_mode": item.answer_mode}
        if actual is not None:
            payload["actual"] = actual
        if artifact_id is not None:
            payload["artifact_id"] = artifact_id
        result = tx.append(
            EventV2(
                id=event_id,
                type=ANSWERED,
                actor="learner",
                user_id=user_id,
                session_id=session_id,
                ws_id=ws_id,
                payload=payload,
            )
        )
        if result.created:
            dispatch(result.event, tx)
        return result.event
