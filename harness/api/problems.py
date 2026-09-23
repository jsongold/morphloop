"""RFC 9457 problem responses (``contracts/openapi/v0.1.yaml``, ``Problem``).

The routing layer never builds an error body by hand: core raises a
:class:`~harness.core.loop.errors.LoopError` carrying the closed ``code`` and
its status, and :func:`problem_body` renders it. The handlers here only add the
two failures that happen before core is reached -- a body that fails its request
schema (422 ``validation-failed``) and a malformed body or query parameter
(400 ``invalid-request``) -- and a catch-all for an unexpected exception
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
from harness.core.loop import LoopError, problem_body
from harness.core.ports import PlainJson

PROBLEM_MEDIA_TYPE = "application/problem+json"

logger = logging.getLogger(__name__)

_STATUS_CODES = {400: "invalid-request", 404: "not-found", 409: "state-conflict"}


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


def loop_problem(error: LoopError) -> JSONResponse:
    """The problem response for a failure core raised."""
    return JSONResponse(
        problem_body(error), status_code=error.status, media_type=PROBLEM_MEDIA_TYPE
    )


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

    @app.exception_handler(LoopError)
    async def _loop_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, LoopError)
        if exc.status >= 500:
            logger.warning("%s on %s: %s", exc.code, request.url.path, exc.detail)
        return loop_problem(exc)

    @app.exception_handler(ContractValidationError)
    async def _contract_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, ContractValidationError)
        return problem(
            status=422,
            code="validation-failed",
            detail=f"the event does not validate against {exc.schema_id}",
            errors=[_split(message) for message in exc.errors],
        )

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
