"""Loop errors mapped to the ``Problem`` codes of the HTTP contract.

The API layer stays thin by catching :class:`LoopError` and rendering
:func:`problem_body` with :attr:`LoopError.status`; the codes are the closed
vocabulary of ``contracts/openapi/v0.1.yaml`` (``components.schemas.Problem``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from harness.core.ports import PlainJson

PROBLEM_TYPE_BASE = "https://morphloop.dev/problems/"


class LoopError(Exception):
    """Base class for failures a learner-facing request can produce."""

    code: ClassVar[str] = "internal"
    status: ClassVar[int] = 500

    def __init__(self, detail: str, *, errors: Sequence[str] = ()) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = tuple(errors)


class InvalidRequestError(LoopError):
    """Malformed or unusable input (400)."""

    code = "invalid-request"
    status = 400


class NotFoundError(LoopError):
    """No such session, attempt, lab, activity or content (404)."""

    code = "not-found"
    status = 404


class StateConflictError(LoopError):
    """The operation is not allowed in the current state (409)."""

    code = "state-conflict"
    status = 409


class ValidationFailedError(LoopError):
    """A body or payload failed its contract schema (422)."""

    code = "validation-failed"
    status = 422


class LLMFailedError(LoopError):
    """An LLM call failed or its output failed validation; no state changed (502)."""

    code = "llm-failed"
    status = 502


class LabUnavailableError(LoopError):
    """The lab runtime could not start, reset or reach a lab (503)."""

    code = "lab-unavailable"
    status = 503


class ReferenceSolutionLeakError(LoopError):
    """A context assembled for the tutor contains reference-solution text (AC-J6).

    This is a harness bug, not a learner error: the solution lives in its own
    projection and is never read on the tutor path. The guard exists so the
    invariant fails loudly instead of silently reaching the learner.
    """

    code = "internal"
    status = 500


def problem_body(error: LoopError) -> dict[str, PlainJson]:
    """RFC 9457 problem details for ``error`` (``Problem``)."""
    body: dict[str, PlainJson] = {
        "type": PROBLEM_TYPE_BASE + error.code,
        "title": error.code.replace("-", " "),
        "status": error.status,
        "code": error.code,
        "detail": error.detail,
    }
    if error.errors:
        body["errors"] = [{"path": "$", "message": message} for message in error.errors]
    return body
