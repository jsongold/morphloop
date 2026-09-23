"""The v0.1 HTTP endpoints (``contracts/openapi/v0.1.yaml``).

Thin by contract: a handler reads the request, calls one
:class:`~harness.core.loop.service.LearningLoop` method and serializes the view
it returns. No handler builds an event, decides a status transition or touches a
projection -- those live in core, and a failure comes back as a
:class:`~harness.core.loop.errors.LoopError` that :mod:`harness.api.problems`
renders as ``application/problem+json``.

Two things the routing layer does own:

- ``Idempotent-Replayed``. Core recognises a resent ``idempotency_key`` and
  returns the original result, but says nothing about it, so the route looks the
  key up in the idempotency projection *before* the call and sets the header
  when it was already there (ADR-0008, AC-F5).
- Keeping the event loop free. Starting or resetting a lab and the evaluation
  chain are synchronous and slow (Docker, then several LLM calls), so they run
  in a worker thread; evaluation runs after the ``202`` has been sent.
"""

from __future__ import annotations

import logging
from typing import Annotated

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from harness.api.backend import Backend
from harness.api.schemas import (
    AttemptStartRequest,
    ChatMessageRequest,
    ClientEventRequest,
    CommandRequest,
    SessionStartRequest,
)
from harness.api.terminal import TerminalRegistry, registry_of
from harness.core.loop import (
    IDEMPOTENCY_PROJECTION,
    AttemptState,
    LabState,
    LoopError,
    NotFoundError,
    SessionState,
    utc_now,
    uuid_ids,
)
from harness.core.pack.model import PackRef
from harness.core.ports import PlainJson, format_timestamp, to_plain_object

logger = logging.getLogger(__name__)

router = APIRouter()

REPLAYED_HEADER = "Idempotent-Replayed"


def backend_of(request: Request) -> Backend:
    """The wired backend; missing only when startup could not build it."""
    backend = getattr(request.app.state, "backend", None)
    if backend is None:
        raise LoopError("the backend could not be wired; see the server log")
    assert isinstance(backend, Backend)
    return backend


Wired = Annotated[Backend, Depends(backend_of)]


def _replayed(backend: Backend, idempotency_key: str) -> bool:
    """Whether an earlier request already used this key (AC-F5)."""
    with backend.store.transaction() as tx:
        return tx.get_projection(IDEMPOTENCY_PROJECTION, idempotency_key) is not None


def _respond(body: dict[str, PlainJson], *, status: int, replayed: bool = False) -> JSONResponse:
    headers = {REPLAYED_HEADER: "true"} if replayed else None
    return JSONResponse(body, status_code=status, headers=headers)


def _session_state(state: SessionState) -> dict[str, PlainJson]:
    """``SessionState`` on the wire. ``last_completed_attempt`` is a core-only
    convenience the OpenAPI component does not carry, so it is dropped here."""
    body = state.to_dict()
    body.pop("last_completed_attempt", None)
    return body


# --- packs, learners --------------------------------------------------------


@router.get("/packs")
def list_packs(backend: Wired) -> dict[str, PlainJson]:
    # Gap: the `Pack` component requires `imported_at`, but nothing records when
    # a pack was imported (the pack projection holds no timestamp and the
    # EventStore Port exposes none), so the field is omitted rather than made up.
    return {"packs": [view.to_dict() for view in backend.loop.list_packs()]}


@router.post("/learners", status_code=201)
def create_learner() -> dict[str, PlainJson]:
    """A learner is an identity only: no event, no idempotency (OpenAPI)."""
    return {"learner_id": uuid_ids("usr"), "created_at": format_timestamp(utc_now())}


@router.get("/learners/{learner_id}/sessions")
def list_learner_sessions(backend: Wired, learner_id: str) -> dict[str, PlainJson]:
    return {"sessions": [view.to_dict() for view in backend.loop.learner_sessions(learner_id)]}


@router.get("/learners/{learner_id}/skills")
def get_learner_skills(
    backend: Wired, learner_id: str, pack_id: str | None = None
) -> dict[str, PlainJson]:
    skills = backend.loop.learner_skills(learner_id, pack_id)
    return {"learner_id": learner_id, "skills": [view.to_dict() for view in skills]}


# --- sessions ---------------------------------------------------------------


def _pack_ref(backend: Backend, pack_id: str, content_hash: str) -> PackRef:
    """The imported pack version the request selected. ``PackRef`` is keyed by
    ``pack_id/pack_version/content_hash`` while the request carries only the id
    and the hash (which is the identity, ADR-0010), so the version is looked up."""
    for view in backend.loop.list_packs(pack_id):
        if view.pack.content_hash == content_hash:
            return view.pack
    raise NotFoundError(f"no imported pack {pack_id!r} with content hash {content_hash!r}")


@router.post("/sessions", status_code=201)
def start_session(backend: Wired, body: SessionStartRequest) -> Response:
    replayed = _replayed(backend, body.idempotency_key)
    state = backend.loop.start_session(
        learner_id=body.learner_id,
        pack=_pack_ref(backend, body.pack_id, body.pack_content_hash),
        idempotency_key=body.idempotency_key,
    )
    return _respond(_session_state(state), status=201, replayed=replayed)


@router.get("/sessions/{session_id}")
def get_session(backend: Wired, session_id: str) -> dict[str, PlainJson]:
    return _session_state(backend.loop.session_state(session_id))


@router.get("/sessions/{session_id}/layout")
def get_session_layout(backend: Wired, session_id: str) -> dict[str, PlainJson]:
    layout = backend.loop.session_layout(session_id)
    return {"layout": None if layout is None else to_plain_object(layout)}


@router.get("/sessions/{session_id}/activities")
def list_session_activities(backend: Wired, session_id: str) -> dict[str, PlainJson]:
    views = backend.loop.session_activities(session_id)
    return {"activities": [view.to_dict() for view in views]}


@router.get("/sessions/{session_id}/content")
def list_session_content(
    backend: Wired, session_id: str, kind: str | None = None
) -> dict[str, PlainJson]:
    return {"items": [s.to_dict() for s in backend.loop.session_content(session_id, kind)]}


@router.get("/sessions/{session_id}/content/{kind}/{definition_id}")
def get_session_content(
    backend: Wired, session_id: str, kind: str, definition_id: str
) -> dict[str, PlainJson]:
    return backend.loop.session_content_document(session_id, kind, definition_id).to_dict()


# --- attempts and labs ------------------------------------------------------


@router.post("/sessions/{session_id}/attempts", status_code=201)
async def start_attempt(backend: Wired, session_id: str, body: AttemptStartRequest) -> Response:
    replayed = await anyio.to_thread.run_sync(_replayed, backend, body.idempotency_key)
    # Starting the activity's lab happens inside this call, synchronously.
    state: AttemptState = await anyio.to_thread.run_sync(
        lambda: backend.loop.start_attempt(
            session_id=session_id,
            activity_definition_id=body.activity_definition_id,
            idempotency_key=body.idempotency_key,
        )
    )
    return _respond(state.to_dict(), status=201, replayed=replayed)


@router.get("/attempts/{attempt_id}")
def get_attempt(backend: Wired, attempt_id: str) -> dict[str, PlainJson]:
    return backend.loop.attempt_state(attempt_id).to_dict()


@router.post("/attempts/{attempt_id}/submit", status_code=202)
def submit_attempt(
    backend: Wired, attempt_id: str, body: CommandRequest, tasks: BackgroundTasks
) -> Response:
    replayed = _replayed(backend, body.idempotency_key)
    state = backend.loop.submit_attempt(attempt_id=attempt_id, idempotency_key=body.idempotency_key)
    if not replayed:
        tasks.add_task(_evaluate, backend, attempt_id)
    return _respond(state.to_dict(), status=202, replayed=replayed)


def _evaluate(backend: Backend, attempt_id: str) -> None:
    """Run the evaluation chain after the ``202``; a failure is recorded on the
    attempt as ``last_submission_error`` by core (AC-E4), so it is only logged."""
    try:
        backend.loop.evaluate_attempt(attempt_id)
    except LoopError as error:
        logger.info("evaluation of %s failed: %s %s", attempt_id, error.code, error.detail)


@router.get("/labs/{lab_instance_id}")
def get_lab(backend: Wired, lab_instance_id: str) -> dict[str, PlainJson]:
    return backend.loop.lab_state(lab_instance_id).to_dict()


@router.post("/labs/{lab_instance_id}/reset", status_code=202)
async def reset_lab(
    request: Request, backend: Wired, lab_instance_id: str, body: CommandRequest
) -> Response:
    replayed = await anyio.to_thread.run_sync(_replayed, backend, body.idempotency_key)
    state: LabState = await anyio.to_thread.run_sync(
        lambda: backend.loop.reset_lab(
            lab_instance_id=lab_instance_id, idempotency_key=body.idempotency_key
        )
    )
    registry: TerminalRegistry = registry_of(request.app)
    await registry.announce_reset(lab_instance_id)
    return _respond(state.to_dict(), status=202, replayed=replayed)


# --- client events, chat, timeline ------------------------------------------


@router.post("/sessions/{session_id}/events", status_code=201)
def append_client_event(backend: Wired, session_id: str, body: ClientEventRequest) -> Response:
    replayed = _replayed(backend, body.idempotency_key)
    event = backend.loop.append_client_event(
        session_id=session_id,
        event_type=body.event_type,
        event_version=body.event_version,
        payload=body.payload,
        attempt_id=body.attempt_id,
        idempotency_key=body.idempotency_key,
        occurred_at=body.occurred_at,
    )
    return _respond({"event": event.to_dict()}, status=201, replayed=replayed)


@router.post("/sessions/{session_id}/chat/messages", status_code=201)
async def send_chat_message(backend: Wired, session_id: str, body: ChatMessageRequest) -> Response:
    replayed = await anyio.to_thread.run_sync(_replayed, backend, body.idempotency_key)
    exchange = await anyio.to_thread.run_sync(
        lambda: backend.loop.send_chat_message(
            session_id=session_id,
            text=body.text,
            references=body.references,
            attempt_id=body.attempt_id,
            thread_id=body.thread_id,
            requested_mode=body.requested_mode,
            idempotency_key=body.idempotency_key,
            occurred_at=body.occurred_at,
        )
    )
    return _respond(exchange.to_dict(), status=201, replayed=replayed)


@router.get("/sessions/{session_id}/chat")
def get_session_chat(backend: Wired, session_id: str) -> dict[str, PlainJson]:
    return {"events": [event.to_dict() for event in backend.loop.session_chat(session_id)]}


@router.get("/sessions/{session_id}/highlights")
def get_session_highlights(backend: Wired, session_id: str) -> dict[str, PlainJson]:
    return {"events": [event.to_dict() for event in backend.loop.session_highlights(session_id)]}


@router.get("/sessions/{session_id}/timeline")
def get_session_timeline(
    backend: Wired,
    session_id: str,
    after_position: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, PlainJson]:
    page = backend.loop.session_timeline(session_id, after_position=after_position, limit=limit)
    return page.to_dict()
