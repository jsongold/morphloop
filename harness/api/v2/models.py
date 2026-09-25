"""Shared request-body building blocks for `/v2` routes (#92).

- `V2Model`: every request model subclasses it; unknown fields are rejected
  (422), matching `additionalProperties: false` in `contracts/openapi/v0.2`.
- `Text`: every free-text string field uses it; NUL (`\\u0000`) is rejected
  (422) because Postgres jsonb cannot store it. Add the schema's own limits on
  top: `Annotated[Text, Field(min_length=1, max_length=N)]`.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


class V2Model(BaseModel):
    """Base for `/v2` request bodies: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain the NUL character (\\u0000)")
    return value


Text = Annotated[str, AfterValidator(_reject_nul)]
