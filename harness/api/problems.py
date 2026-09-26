"""RFC 9457 problem responses (``contracts/openapi/v0.2/components/common.yaml``,
``Problem``).

Routes return :func:`problem` for their own failures. The handlers here add the
failures that happen before a route answers -- an event that fails its contract
schema (422 ``validation-failed``), a body that fails its request
schema (422 ``validation-failed``) and a malformed body or query parameter
(400 ``invalid-request``) -- a reused ``Idempotency-Key`` with a different
body (409 ``idempotency-key-reused``, v2), a missing or invalid bearer token
(401 ``unauthorized``), an unreachable identity provider (503
``auth-unavailable``), and a catch-all for an unexpected exception
(500 ``internal``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from harness.core.contract_schemas import ContractValidationError
from harness.core.ports import PlainJson
from harness.core.ports.auth import AuthError, AuthUnavailableError
from harness.core.ports.events_v2 import EventIdConflictError

PROBLEM_MEDIA_TYPE = "application/problem+json"

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "invalid-request",
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    409: "state-conflict",
}


def problem(
    *, status: int, code: str, detail: str, errors: list[dict[str, str]] | None = None
) -> JSONResponse:
    """A ``Problem`` response body (RFC 9457)."""
    body: dict[str, PlainJson] = {
        "type": f"https://morphloop.dev/problems/{code}",
        "title": code.replace("-", " "),
        "status": status,
        "code": code,
        "detail": detail,
    }
    if errors is not None:
        body["errors"] = [dict(item) for item in errors]
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE)


def _split(message: str) -> dict[str, str]:
    """``"$.payload.x: is too long"`` -> ``{"path": ..., "message": ...}``."""
    path, separator, detail = message.partition(": ")
    return {"path": path, "message": detail} if separator else {"path": "$", "message": message}


def _location(error: Any) -> str:
    parts = ["$"]
    for segment in list(error.get("loc", ()))[1:]:
        parts.append(f"[{segment}]" if isinstance(segment, int) else f".{segment}")
    return "".join(parts)


def install_handlers(app: FastAPI) -> None:
    """Register the exception handlers on ``app`` (also used on WebSocket routes,
    where Starlette turns the response into a handshake denial)."""

    @app.exception_handler(ContractValidationError)
    async def _contract_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, ContractValidationError)
        return problem(
            status=422,
            code="validation-failed",
            detail=f"the event does not validate against {exc.schema_id}",
            errors=[_split(message) for message in exc.errors],
        )

    @app.exception_handler(EventIdConflictError)
    async def _event_id_conflict(request: Request, exc: Exception) -> Response:
        # A reused Idempotency-Key with a different body (v2 routes).
        return problem(status=409, code="idempotency-key-reused", detail=str(exc))

    @app.exception_handler(AuthError)
    async def _unauthorized(request: Request, exc: Exception) -> Response:
        response = problem(status=401, code="unauthorized", detail=str(exc))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(AuthUnavailableError)
    async def _auth_unavailable(request: Request, exc: Exception) -> Response:
        return problem(status=503, code="auth-unavailable", detail=str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, RequestValidationError)
        errors = exc.errors()
        # A body that parsed but does not match its schema is 422; malformed
        # JSON and bad path/query parameters are 400 (the Problem vocabulary).
        body_schema = errors and all(
            error["loc"][:1] == ("body",) and error["type"] != "json_invalid" for error in errors
        )
        if body_schema:
            return problem(
                status=422,
                code="validation-failed",
                detail="the request body does not match its schema",
                errors=[{"path": _location(e), "message": e["msg"]} for e in errors],
            )
        return problem(
            status=400,
            code="invalid-request",
            detail="; ".join(f"{_location(e)}: {e['msg']}" for e in errors) or "malformed request",
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, HTTPException)
        code = _STATUS_CODES.get(exc.status_code, "internal")
        return problem(status=exc.status_code, code=code, detail=str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error on %s", request.url.path)
        return problem(status=500, code="internal", detail=type(exc).__name__)
