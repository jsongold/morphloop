"""``/v2/sessions``: topic selection and the pinned topic subtree (#34, #54).

``POST /sessions`` copies the chosen topic's subtree out of the loaded pack
and appends ``session.created``; ``GET /sessions`` and
``GET /sessions/{session_id}`` read the :class:`~harness.core.session.model.
SessionView` it builds. Sittings (ADR-0018: derived, never stored) are added
to the detail response only when ``idle_minutes`` is given -- the app has no
setting for it (a later issue).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from harness.api.v2.deps import (
    EventIdDep,
    EventStoreV2Dep,
    EventTransactionV2Dep,
    PackV2Dep,
    UserIdDep,
)
from harness.core.ports import JsonObject, PlainJson, format_timestamp, to_plain_object
from harness.core.ports.events_v2 import EventIdConflictError
from harness.core.session.service import (
    PackMismatchError,
    TopicNotFoundError,
    create_session,
    get_session,
    list_sessions,
)
from harness.core.session.sittings import MAX_IDLE_MINUTES
from harness.core.session.sittings import sittings as derive_sittings

router = APIRouter(tags=["session"])

# Mirrors contracts/schemas/common/ids.json#/$defs/pack_id and #/$defs/definition_id:
# a request field that ends up in an event payload carries the payload schema's
# own constraints, so out-of-schema input is a 422 here, never a contract
# failure inside tx.append.
_PACK_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
_TOPIC_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(pattern=_PACK_ID_PATTERN)
    topic_id: str = Field(pattern=_TOPIC_ID_PATTERN)


def _document(doc: JsonObject) -> dict[str, PlainJson]:
    # `position` is internal ordering only (see SessionView.apply); the
    # response schema is `additionalProperties: false` and does not list it.
    plain = to_plain_object(doc)
    plain.pop("position", None)
    return plain


@router.post("/sessions", status_code=201)
def create_session_route(
    body: CreateSessionRequest,
    tx: EventTransactionV2Dep,
    pack: PackV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
) -> dict[str, PlainJson]:
    try:
        doc = create_session(
            tx,
            pack=pack,
            user_id=user_id,
            event_id=event_id,
            pack_id=body.pack_id,
            topic_id=body.topic_id,
        )
    except PackMismatchError as exc:
        raise HTTPException(
            status_code=404, detail=f"pack {exc} is not the pack this server has loaded"
        ) from exc
    except TopicNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no topic {exc} in the pack") from exc
    except EventIdConflictError as exc:
        raise HTTPException(
            status_code=409, detail="Idempotency-Key was already used with a different request"
        ) from exc
    return _document(doc)


@router.get("/sessions")
def list_sessions_route(
    tx: EventTransactionV2Dep, user_id: UserIdDep
) -> list[dict[str, PlainJson]]:
    return [_document(doc) for doc in list_sessions(tx, user_id=user_id)]


@router.get("/sessions/{session_id}")
def get_session_route(
    session_id: str,
    store: EventStoreV2Dep,
    user_id: UserIdDep,
    idle_minutes: Annotated[int | None, Query(ge=1, le=MAX_IDLE_MINUTES)] = None,
) -> dict[str, PlainJson]:
    # Read the events (if any) before opening the transaction below, so this
    # request never holds two pool connections at once (#89 review: the
    # `EventTransactionV2Dep` used to stay open across a second, separate
    # `store.read` connection).
    events = store.read(session_id=session_id) if idle_minutes is not None else ()
    with store.transaction() as tx:
        doc = get_session(tx, session_id, user_id=user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
    result = _document(doc)
    if idle_minutes is not None:
        result["sittings"] = [
            {
                "started_at": format_timestamp(sitting.started_at),
                "ended_at": format_timestamp(sitting.ended_at),
            }
            for sitting in derive_sittings(events, idle_minutes=idle_minutes)
        ]
    return result
