"""Loop projections: pure appliers over the event log (ADR-0008, AC-F6).

Every projection document is a pure function of the previous document and one
stored event, so the live path and :func:`rebuild_projections` share exactly the
same code. Nothing here calls an LLM: ``learner_skill.updated`` re-applies the
update result recorded on the event (ADR-0013).

Projections written here (all prefixed ``loop_``; the pack projections belong to
:mod:`harness.core.pack` and are never rebuilt from events):

``loop_session``
    key ``<session_id>``; session identity, pack ref, last position, attempts
    and the restorable UI state.
``loop_learner_session``
    key ``<learner_id>/<position>``; index for "list a learner's sessions".
``loop_attempt``
    key ``<attempt_id>``; attempt status, its labs, the event ids of its
    evaluation chain and the problem of the last failed evaluation
    (``evaluation.failed``), cleared by the next submission.
``loop_lab``
    key ``<lab_instance_id>``; lab lifecycle, provenance and the opaque
    ``runtime_ref`` a terminal attaches to (``lab.started`` v2; ``None`` for v1).
``loop_learner_skill``
    key ``<learner_id>/<pack_id>/<skill_id>``; the learner-skill state.
``loop_highlight``
    key ``<session_id>/<highlight_id>``; the stored ``content.highlighted``
    event, so a highlight survives a reload (AC-D2).
``loop_chat``
    key ``<session_id>/<position>``; the stored chat events (AC-F4).
``loop_memo``
    key ``<session_id>/<memo_id>``; the current memo of one highlight thread
    (``memo.recorded`` upserts, ``memo.edited`` marks it learner-owned).
``loop_idempotency``
    key ``<idempotency_key>``; which event a key produced, so a resend can
    return the original ids instead of appending a second event (AC-F5). The
    store itself keys idempotency, but its Port offers no lookup by key, and a
    service that generates ids (a session id, an attempt id) must find the
    original before it builds a second envelope.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from harness.core.ports import (
    EventStore,
    EventTransaction,
    JsonObject,
    PlainJson,
    StoredEvent,
    format_timestamp,
    to_plain_object,
)

SESSION_PROJECTION = "loop_session"
LEARNER_SESSION_PROJECTION = "loop_learner_session"
ATTEMPT_PROJECTION = "loop_attempt"
LAB_PROJECTION = "loop_lab"
LEARNER_SKILL_PROJECTION = "loop_learner_skill"
HIGHLIGHT_PROJECTION = "loop_highlight"
CHAT_PROJECTION = "loop_chat"
MEMO_PROJECTION = "loop_memo"
IDEMPOTENCY_PROJECTION = "loop_idempotency"

LOOP_PROJECTIONS: tuple[str, ...] = (
    SESSION_PROJECTION,
    LEARNER_SESSION_PROJECTION,
    ATTEMPT_PROJECTION,
    LAB_PROJECTION,
    LEARNER_SKILL_PROJECTION,
    HIGHLIGHT_PROJECTION,
    CHAT_PROJECTION,
    MEMO_PROJECTION,
    IDEMPOTENCY_PROJECTION,
)

_REBUILD_PAGE = 500
_POSITION_KEY_WIDTH = 20

type Document = dict[str, PlainJson]
type Mutator = Callable[[JsonObject | None], JsonObject]


class ProjectionError(RuntimeError):
    """The event log and the projections disagree (a producer bug)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionWrite:
    """One document to insert or replace, as a pure function of the current one."""

    projection: str
    key: str
    apply: Mutator


def position_key(prefix: str, position: int) -> str:
    """Key that sorts by ``position`` inside ``prefix`` (projection keys sort as text)."""
    return f"{prefix}/{position:0{_POSITION_KEY_WIDTH}d}"


def learner_skill_key(learner_id: str, pack_id: str, skill_id: str) -> str:
    return f"{learner_id}/{pack_id}/{skill_id}"


def highlight_key(session_id: str, highlight_id: str) -> str:
    return f"{session_id}/{highlight_id}"


def memo_key(session_id: str, memo_id: str) -> str:
    return f"{session_id}/{memo_id}"


# --- small accessors --------------------------------------------------------


def _doc(document: JsonObject | None, where: str) -> Document:
    if document is None:
        raise ProjectionError(f"{where}: no projection document yet")
    return to_plain_object(document)


def _payload(event: StoredEvent) -> Document:
    return to_plain_object(event.payload)


def _string(document: Mapping[str, PlainJson], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str):
        raise ProjectionError(f"projection field {name!r} is not a string")
    return value


def _strings(document: Mapping[str, PlainJson], name: str) -> list[PlainJson]:
    value = document.get(name)
    if not isinstance(value, list):
        raise ProjectionError(f"projection field {name!r} is not an array")
    return list(value)


def _object(value: PlainJson | None, name: str) -> Document:
    if not isinstance(value, dict):
        raise ProjectionError(f"projection field {name!r} is not an object")
    return value


def _require_attempt(event: StoredEvent) -> tuple[str, str]:
    if event.attempt_id is None or event.activity_definition_id is None:
        raise ProjectionError(f"{event.event_type} must carry attempt context")
    return event.attempt_id, event.activity_definition_id


def _append_id(document: Document, name: str, value: str) -> None:
    ids = _strings(document, name)
    if value not in ids:
        ids.append(value)
    document[name] = ids


# --- writes per event type --------------------------------------------------


def _new_session(event: StoredEvent) -> Document:
    payload = _payload(event)
    provenance = _object(payload.get("provenance"), "provenance")
    return {
        "session_id": event.session_id,
        "learner_id": event.learner_id,
        "pack": _object(provenance.get("pack"), "provenance.pack"),
        "provenance": provenance,
        "started_at": format_timestamp(event.occurred_at),
        "session_started_event_id": event.event_id,
        "last_position": event.position,
        "attempt_ids": [],
        "active_attempt_id": None,
        "thread_ids": [],
        "memo_ids": [],
        "ui_state": {"open_content": None, "visualization_steps": {}},
    }


def _session_write(
    event: StoredEvent, mutate: Callable[[Document, StoredEvent], None] | None = None
) -> ProjectionWrite:
    def apply(document: JsonObject | None) -> JsonObject:
        if event.event_type == "session.started":
            current = _new_session(event)
        else:
            current = _doc(document, f"session {event.session_id}")
        current["last_position"] = event.position
        if mutate is not None:
            mutate(current, event)
        return current

    return ProjectionWrite(projection=SESSION_PROJECTION, key=event.session_id, apply=apply)


def _learner_session_write(event: StoredEvent) -> ProjectionWrite:
    def apply(document: JsonObject | None) -> JsonObject:
        return {
            "learner_id": event.learner_id,
            "session_id": event.session_id,
            "started_at": format_timestamp(event.occurred_at),
            "position": event.position,
        }

    return ProjectionWrite(
        projection=LEARNER_SESSION_PROJECTION,
        key=position_key(event.learner_id, event.position),
        apply=apply,
    )


def _attempt_write(
    event: StoredEvent, mutate: Callable[[Document, StoredEvent], None]
) -> ProjectionWrite:
    attempt_id, _ = _require_attempt(event)

    def apply(document: JsonObject | None) -> JsonObject:
        current = _doc(document, f"attempt {attempt_id}")
        mutate(current, event)
        return current

    return ProjectionWrite(projection=ATTEMPT_PROJECTION, key=attempt_id, apply=apply)


def _activity_started(event: StoredEvent) -> list[ProjectionWrite]:
    attempt_id, definition_id = _require_attempt(event)
    payload = _payload(event)

    def new_attempt(document: JsonObject | None) -> JsonObject:
        return {
            "attempt_id": attempt_id,
            "session_id": event.session_id,
            "learner_id": event.learner_id,
            "activity_definition_id": definition_id,
            "activity_definition_hash": _string(payload, "activity_definition_hash"),
            "status": "active",
            "started_at": format_timestamp(event.occurred_at),
            "started_event_id": event.event_id,
            "lab_instance_id": None,
            "lab_instance_ids": [],
            "submission_event_ids": [],
            "evaluation_event_id": None,
            "evaluation_id": None,
            "evaluation_position": None,
            "evidence_event_ids": [],
            "skill_update_event_ids": [],
            "completion_event_id": None,
            "outcome": None,
            "last_submission_error": None,
        }

    def on_session(document: Document, _: StoredEvent) -> None:
        _append_id(document, "attempt_ids", attempt_id)
        document["active_attempt_id"] = attempt_id

    return [
        ProjectionWrite(projection=ATTEMPT_PROJECTION, key=attempt_id, apply=new_attempt),
        _session_write(event, on_session),
    ]


def _lab_started(event: StoredEvent) -> list[ProjectionWrite]:
    attempt_id, _ = _require_attempt(event)
    payload = _payload(event)
    lab_instance_id = _string(payload, "lab_instance_id")
    replaces = payload.get("replaces_lab_instance_id")

    def new_lab(document: JsonObject | None) -> JsonObject:
        return {
            "lab_instance_id": lab_instance_id,
            "attempt_id": attempt_id,
            "session_id": event.session_id,
            "environment": _object(payload.get("environment"), "environment"),
            "provenance": _object(payload.get("provenance"), "provenance"),
            "trigger": _string(payload, "trigger"),
            "replaces_lab_instance_id": replaces,
            "replaced_by_lab_instance_id": None,
            "started_event_id": event.event_id,
            "reset_event_id": None,
            "started_at": format_timestamp(event.occurred_at),
            "runtime_ref": payload.get("runtime_ref"),  # v1 upcast: absent -> None
        }

    def on_attempt(document: Document, _: StoredEvent) -> None:
        document["lab_instance_id"] = lab_instance_id
        _append_id(document, "lab_instance_ids", lab_instance_id)

    writes = [
        ProjectionWrite(projection=LAB_PROJECTION, key=lab_instance_id, apply=new_lab),
        _attempt_write(event, on_attempt),
        _session_write(event),
    ]
    if isinstance(replaces, str):

        def on_replaced(document: JsonObject | None) -> JsonObject:
            current = _doc(document, f"lab {replaces}")
            current["replaced_by_lab_instance_id"] = lab_instance_id
            return current

        writes.append(ProjectionWrite(projection=LAB_PROJECTION, key=replaces, apply=on_replaced))
    return writes


def _lab_reset(event: StoredEvent) -> list[ProjectionWrite]:
    lab_instance_id = _string(_payload(event), "lab_instance_id")

    def on_lab(document: JsonObject | None) -> JsonObject:
        current = _doc(document, f"lab {lab_instance_id}")
        current["reset_event_id"] = event.event_id
        return current

    return [
        ProjectionWrite(projection=LAB_PROJECTION, key=lab_instance_id, apply=on_lab),
        _session_write(event),
    ]


def _lab_stopped(event: StoredEvent) -> list[ProjectionWrite]:
    lab_instance_id = _string(_payload(event), "lab_instance_id")

    def on_lab(document: JsonObject | None) -> JsonObject:
        current = _doc(document, f"lab {lab_instance_id}")
        current["stopped_event_id"] = event.event_id
        return current

    return [
        ProjectionWrite(projection=LAB_PROJECTION, key=lab_instance_id, apply=on_lab),
        _session_write(event),
    ]


def _activity_submitted(event: StoredEvent) -> list[ProjectionWrite]:
    def on_attempt(document: Document, _: StoredEvent) -> None:
        document["status"] = "evaluating"
        document["last_submission_error"] = None
        _append_id(document, "submission_event_ids", event.event_id)

    return [_attempt_write(event, on_attempt), _session_write(event)]


def _evaluation_failed(event: StoredEvent) -> list[ProjectionWrite]:
    problem = _object(_payload(event).get("problem"), "problem")

    def on_attempt(document: Document, _: StoredEvent) -> None:
        document["status"] = "active"
        document["last_submission_error"] = problem

    return [_attempt_write(event, on_attempt), _session_write(event)]


def _evaluation_completed(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)

    def on_attempt(document: Document, _: StoredEvent) -> None:
        document["evaluation_event_id"] = event.event_id
        document["evaluation_id"] = _string(payload, "evaluation_id")
        document["evaluation_position"] = event.position
        document["evidence_event_ids"] = []
        document["skill_update_event_ids"] = []

    return [_attempt_write(event, on_attempt), _session_write(event)]


def _evidence_created(event: StoredEvent) -> list[ProjectionWrite]:
    def on_attempt(document: Document, _: StoredEvent) -> None:
        _append_id(document, "evidence_event_ids", event.event_id)

    return [_attempt_write(event, on_attempt), _session_write(event)]


def _learner_skill_updated(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)
    pack_id = _string(payload, "pack_id")
    skill_id = _string(payload, "skill_id")

    def on_skill(document: JsonObject | None) -> JsonObject:
        previous = to_plain_object(document) if document is not None else {}
        count = previous.get("update_count")
        return {
            "learner_id": event.learner_id,
            "pack_id": pack_id,
            "skill_id": skill_id,
            "state": _object(payload.get("next"), "next"),
            "update_count": (count if isinstance(count, int) else 0) + 1,
            "last_update_event_id": event.event_id,
            "updated_at": format_timestamp(event.occurred_at),
        }

    writes = [
        ProjectionWrite(
            projection=LEARNER_SKILL_PROJECTION,
            key=learner_skill_key(event.learner_id, pack_id, skill_id),
            apply=on_skill,
        ),
        _session_write(event),
    ]
    if event.attempt_id is not None:

        def on_attempt(document: Document, _: StoredEvent) -> None:
            _append_id(document, "skill_update_event_ids", event.event_id)

        writes.append(_attempt_write(event, on_attempt))
    return writes


def _activity_completed(event: StoredEvent) -> list[ProjectionWrite]:
    attempt_id, _ = _require_attempt(event)
    payload = _payload(event)

    def on_attempt(document: Document, _: StoredEvent) -> None:
        document["status"] = "completed"
        document["outcome"] = _string(payload, "outcome")
        document["completion_event_id"] = event.event_id

    def on_session(document: Document, _: StoredEvent) -> None:
        if document.get("active_attempt_id") == attempt_id:
            document["active_attempt_id"] = None

    return [_attempt_write(event, on_attempt), _session_write(event, on_session)]


def _content_opened(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)

    def on_session(document: Document, _: StoredEvent) -> None:
        ui_state = _object(document.get("ui_state"), "ui_state")
        ui_state["open_content"] = payload
        document["ui_state"] = ui_state

    return [_session_write(event, on_session)]


def _visualization_step_selected(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)

    def on_session(document: Document, _: StoredEvent) -> None:
        ui_state = _object(document.get("ui_state"), "ui_state")
        steps = _object(ui_state.get("visualization_steps"), "ui_state.visualization_steps")
        steps[_string(payload, "visualization_id")] = payload
        ui_state["visualization_steps"] = steps
        document["ui_state"] = ui_state

    return [_session_write(event, on_session)]


def _content_highlighted(event: StoredEvent) -> list[ProjectionWrite]:
    highlight_id = _string(_payload(event), "highlight_id")

    def on_highlight(document: JsonObject | None) -> JsonObject:
        return {
            "highlight_id": highlight_id,
            "session_id": event.session_id,
            "position": event.position,
            "event": event.to_dict(),
        }

    return [
        ProjectionWrite(
            projection=HIGHLIGHT_PROJECTION,
            key=highlight_key(event.session_id, highlight_id),
            apply=on_highlight,
        ),
        _session_write(event),
    ]


def _chat(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)
    thread_id = _string(payload, "thread_id")

    def on_chat(document: JsonObject | None) -> JsonObject:
        return {
            "session_id": event.session_id,
            "thread_id": thread_id,
            "message_id": _string(payload, "message_id"),
            "position": event.position,
            "event": event.to_dict(),
        }

    def on_session(document: Document, _: StoredEvent) -> None:
        _append_id(document, "thread_ids", thread_id)

    return [
        ProjectionWrite(
            projection=CHAT_PROJECTION,
            key=position_key(event.session_id, event.position),
            apply=on_chat,
        ),
        _session_write(event, on_session),
    ]


def _memo_recorded(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)
    memo_id = _string(payload, "memo_id")

    def on_memo(document: JsonObject | None) -> JsonObject:
        current = _doc(document, f"memo {memo_id}") if document is not None else {}
        current["memo_id"] = memo_id
        current["highlight_id"] = _string(payload, "highlight_id")
        current["thread_id"] = _string(payload, "thread_id")
        current["title"] = _string(payload, "title")
        current["body"] = _string(payload, "body")
        current["source_event_ids"] = _strings(payload, "source_event_ids")
        current["edited_by_learner"] = current.get("edited_by_learner", False)
        current["updated_at"] = format_timestamp(event.occurred_at)
        current["last_event_id"] = event.event_id
        current["first_recorded_position"] = current.get("first_recorded_position", event.position)
        return current

    def on_session(document: Document, _: StoredEvent) -> None:
        _append_id(document, "memo_ids", memo_id)

    return [
        ProjectionWrite(
            projection=MEMO_PROJECTION, key=memo_key(event.session_id, memo_id), apply=on_memo
        ),
        _session_write(event, on_session),
    ]


def _memo_edited(event: StoredEvent) -> list[ProjectionWrite]:
    payload = _payload(event)
    memo_id = _string(payload, "memo_id")

    def on_memo(document: JsonObject | None) -> JsonObject:
        current = _doc(document, f"memo {memo_id}")
        current["title"] = _string(payload, "title")
        current["body"] = _string(payload, "body")
        current["edited_by_learner"] = True
        current["updated_at"] = format_timestamp(event.occurred_at)
        current["last_event_id"] = event.event_id
        return current

    return [
        ProjectionWrite(
            projection=MEMO_PROJECTION, key=memo_key(event.session_id, memo_id), apply=on_memo
        ),
        _session_write(event),
    ]


_HANDLERS: Mapping[str, Callable[[StoredEvent], list[ProjectionWrite]]] = {
    "session.started": lambda e: [_session_write(e), _learner_session_write(e)],
    "activity.started": _activity_started,
    "lab.started": _lab_started,
    "lab.reset": _lab_reset,
    "lab.stopped": _lab_stopped,
    "terminal.command": lambda e: [_session_write(e)],
    "terminal.output": lambda e: [_session_write(e)],
    "activity.submitted": _activity_submitted,
    "evaluation.completed": _evaluation_completed,
    "evaluation.failed": _evaluation_failed,
    "evidence.created": _evidence_created,
    "learner_skill.updated": _learner_skill_updated,
    "activity.completed": _activity_completed,
    "content.opened": _content_opened,
    "content.highlighted": _content_highlighted,
    "visualization.step_selected": _visualization_step_selected,
    "assistant.message_requested": _chat,
    "assistant.message_generated": _chat,
    "memo.recorded": _memo_recorded,
    "memo.edited": _memo_edited,
}


def _idempotency_write(event: StoredEvent) -> ProjectionWrite:
    def apply(document: JsonObject | None) -> JsonObject:
        return {
            "idempotency_key": event.idempotency_key,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "session_id": event.session_id,
            "attempt_id": event.attempt_id,
            "position": event.position,
        }

    assert event.idempotency_key is not None
    return ProjectionWrite(
        projection=IDEMPOTENCY_PROJECTION, key=event.idempotency_key, apply=apply
    )


def projection_writes(event: StoredEvent) -> Sequence[ProjectionWrite]:
    """The documents ``event`` updates, each as a pure function of the current one."""
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        raise ProjectionError(f"no projection applier for event type {event.event_type!r}")
    writes = list(handler(event))
    if event.idempotency_key is not None:
        writes.append(_idempotency_write(event))
    return writes


def apply_event(tx: EventTransaction, event: StoredEvent) -> None:
    """Apply ``event`` to the loop projections inside ``tx`` (same transaction as the append)."""
    for write in projection_writes(event):
        current = tx.get_projection(write.projection, write.key)
        tx.put_projection(write.projection, write.key, write.apply(current))


def rebuild_projections(store: EventStore, *, page_size: int = _REBUILD_PAGE) -> int:
    """Drop the loop projections and rebuild them from the log (AC-F6).

    Returns the number of events replayed. Learner state comes from the update
    results stored on ``learner_skill.updated``; no LLM is called (ADR-0013).
    """
    replayed = 0
    with store.transaction() as tx:
        for projection in LOOP_PROJECTIONS:
            tx.clear_projection(projection)
        after = 0
        while True:
            page = store.read_all(after_position=after, limit=page_size)
            if not page:
                return replayed
            for event in page:
                apply_event(tx, event)
                after = event.position
                replayed += 1
