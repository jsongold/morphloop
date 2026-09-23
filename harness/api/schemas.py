"""Request bodies of ``contracts/openapi/v0.1.yaml``.

Only the *request* side is modelled here. Responses are the views of
:mod:`harness.core.loop.views`, which already serialize in the shape of their
OpenAPI component, so the routes never build a response body by hand.

Every model forbids unknown fields, mirroring ``additionalProperties: false``;
a field that the contract requires but allows to be null is declared without a
default, so omitting it is a validation error rather than a silent null. The
payload of a client event is *not* redefined here: it is validated by the event
payload schema itself inside core's append path (``ClientEventRequest``
``allOf``s ``events/dispatch.json``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandRequest(_Body):
    """``CommandRequest``: a command whose only input is its idempotency key."""

    idempotency_key: str


class SessionStartRequest(_Body):
    """``SessionStartRequest``."""

    idempotency_key: str
    learner_id: str
    pack_id: str
    pack_content_hash: str


class AttemptStartRequest(_Body):
    """``AttemptStartRequest``."""

    idempotency_key: str
    activity_definition_id: str


class ClientEventRequest(_Body):
    """``ClientEventRequest``: the producer part of a learner UI event append."""

    event_type: Literal["content.highlighted", "content.opened", "visualization.step_selected"]
    event_version: int
    occurred_at: AwareDatetime
    idempotency_key: str
    attempt_id: str | None
    payload: dict[str, Any]


class ChatMessageRequest(_Body):
    """``ChatMessageRequest``."""

    idempotency_key: str
    occurred_at: AwareDatetime
    thread_id: str | None
    attempt_id: str | None
    text: str
    requested_mode: str | None = None
    references: list[dict[str, Any]]
