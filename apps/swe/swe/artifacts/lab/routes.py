"""`/v2` lab artifact routes (#62, #95): start, read, reset, stop and check labs.

The wired :class:`LabArtifactService` is built once and cached on
`app.state.artifact_lab` (tests set it there first): the Docker lab runtime,
its PTY bridge and the DNS domain adapter. Artifact specs come from the shared
`PackV2Dep`; the learner from `UserIdDep` (never from the request: auth later
swaps what that dependency returns), the event id from `EventIdDep`.

Each request runs on its own `EventTransactionV2Dep`: the ws is resolved through
the ``ws`` view on it (`ws_or_404`, 404 unless it is this learner's) and the
service reads and appends on the same connection (#103). A background task
started with the app stops labs idle past their spec's `idle_seconds`; it is
idempotent, so several API workers may run it (`LabArtifactService.reap_idle`).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from harness.sdk import (
    DockerLabRuntime,
    DockerTerminalBridge,
    DomainAdapterRegistry,
    EventIdDep,
    EventTransactionV2,
    EventTransactionV2Dep,
    JsonObject,
    PackV2,
    PackV2Dep,
    UserIdDep,
    build_event_store_v2,
    problem,
    to_plain_json,
    ws_or_404,
)
from swe.artifacts.lab.service import ArtifactError, LabArtifactService

REAP_INTERVAL_SECONDS = 30.0

logger = logging.getLogger(__name__)


def lab_specs(pack: PackV2) -> dict[str, JsonObject]:
    """Artifact spec id -> spec document."""
    return {str(doc["id"]): doc for doc in pack.documents["artifacts"].values()}


def build_artifact_service(app: Any) -> LabArtifactService:
    """The real service; raises when Docker or the database cannot be reached."""
    import docker  # noqa: PLC0415 - only a real service needs the daemon

    import domains.dns  # noqa: PLC0415 - SWE's domain adapter; moves here with #69

    adapters = DomainAdapterRegistry()
    adapters.register(domains.dns.adapter())
    store = getattr(app.state, "event_store_v2", None) or build_event_store_v2()
    app.state.event_store_v2 = store
    return LabArtifactService(
        store=store,
        labs=DockerLabRuntime(docker.from_env()),
        terminals=DockerTerminalBridge(),
        adapters=adapters,
    )


def artifact_service_of(connection: HTTPConnection) -> LabArtifactService:
    """The wired service (HTTP or WebSocket); built and cached on first use."""
    service: LabArtifactService | None = getattr(connection.app.state, "artifact_lab", None)
    if service is None:
        try:
            service = build_artifact_service(connection.app)
        except Exception as exc:
            logger.exception("could not wire the lab artifact service")
            detail = f"lab artifacts are not wired: {exc}"
            raise ArtifactError(503, "lab-unavailable", detail) from exc
        connection.app.state.artifact_lab = service
    return service


@contextlib.asynccontextmanager
async def _reap_idle_labs(app: FastAPI) -> AsyncIterator[None]:
    async def loop() -> None:
        while True:
            await anyio.sleep(REAP_INTERVAL_SECONDS)
            service = getattr(app.state, "artifact_lab", None)
            pack = getattr(app.state, "pack_v2", None)
            if isinstance(service, LabArtifactService) and isinstance(pack, PackV2):
                try:
                    await anyio.to_thread.run_sync(service.reap_idle, lab_specs(pack))
                except Exception:
                    logger.exception("idle lab reaping failed")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(loop)
        yield
        tasks.cancel_scope.cancel()


router = APIRouter(tags=["artifact"], lifespan=_reap_idle_labs)


class StartArtifact(BaseModel):
    spec_id: str = Field(min_length=1)


class RunCheck(BaseModel):
    check_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


def _run(
    request: Request, action: Callable[[LabArtifactService], JsonObject], status_code: int = 200
) -> JSONResponse:
    try:
        result = action(artifact_service_of(request))
    except ArtifactError as error:
        return problem(status=error.status, code=error.code, detail=error.detail)
    return JSONResponse(to_plain_json(result), status_code=status_code)


def _session_of(tx: EventTransactionV2, ws_id: str, user_id: str, event_id: str) -> str | None:
    """The ws's session for a new start; a resend takes it from the stored event, so
    the ws lookup (404 for another learner's ws) only runs for a new request."""
    existing = tx.get(event_id)
    if existing is not None:
        return existing.session_id
    session_id = ws_or_404(tx, ws_id, user_id=user_id)["session_id"]
    return None if session_id is None else str(session_id)


@router.post("/ws/{ws_id}/artifacts", status_code=201)
def start_artifact(
    request: Request,
    ws_id: str,
    body: StartArtifact,
    pack: PackV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    tx: EventTransactionV2Dep,
) -> JSONResponse:
    specs = lab_specs(pack)
    session_id = _session_of(tx, ws_id, user_id, event_id)
    return _run(
        request,
        lambda s: s.start(
            tx,
            specs,
            user_id=user_id,
            session_id=session_id,
            ws_id=ws_id,
            spec_id=body.spec_id,
            event_id=event_id,
        ),
        201,
    )


@router.get("/ws/{ws_id}/artifacts/{artifact_id}")
def get_artifact(
    request: Request, ws_id: str, artifact_id: str, user_id: UserIdDep, tx: EventTransactionV2Dep
) -> JSONResponse:
    return _run(request, lambda s: s.get(tx, artifact_id, user_id=user_id, ws_id=ws_id))


@router.post("/ws/{ws_id}/artifacts/{artifact_id}/reset")
def reset_artifact(
    request: Request,
    ws_id: str,
    artifact_id: str,
    pack: PackV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    tx: EventTransactionV2Dep,
) -> JSONResponse:
    specs = lab_specs(pack)
    return _run(
        request,
        lambda s: s.reset(tx, specs, artifact_id, event_id=event_id, user_id=user_id, ws_id=ws_id),
    )


@router.post("/ws/{ws_id}/artifacts/{artifact_id}/stop")
def stop_artifact(
    request: Request,
    ws_id: str,
    artifact_id: str,
    user_id: UserIdDep,
    event_id: EventIdDep,
    tx: EventTransactionV2Dep,
) -> JSONResponse:
    return _run(
        request,
        lambda s: s.stop(tx, artifact_id, event_id=event_id, user_id=user_id, ws_id=ws_id),
    )


@router.post("/ws/{ws_id}/artifacts/{artifact_id}/check")
def check_artifact(
    request: Request,
    ws_id: str,
    artifact_id: str,
    body: RunCheck,
    pack: PackV2Dep,
    user_id: UserIdDep,
    event_id: EventIdDep,
    tx: EventTransactionV2Dep,
) -> JSONResponse:
    specs = lab_specs(pack)
    return _run(
        request,
        lambda s: s.check(
            tx,
            specs,
            artifact_id,
            body.check_id,
            body.params,
            event_id=event_id,
            user_id=user_id,
            ws_id=ws_id,
        ),
    )
