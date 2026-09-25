"""Shared request-body building blocks for `/v2` routes (#92).

- `V2Model`: every request model subclasses it; unknown fields are rejected
  (422), matching `additionalProperties: false` in `contracts/openapi/v0.2`,
  and so are NaN / Infinity floats (not JSON; the store refuses them).
- `Text`: every free-text string field uses it; NUL (`\\u0000`) and unpaired
  surrogates are rejected (422): Postgres jsonb cannot store NUL and a lone
  surrogate cannot be encoded as UTF-8. Add the schema's own limits on
  top: `Annotated[Text, Field(min_length=1, max_length=N)]`.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


class V2Model(BaseModel):
    """Base for `/v2` request bodies: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _storable(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain the NUL character (\\u0000)")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must not contain unpaired surrogates (not valid UTF-8)") from None
    return value


Text = Annotated[str, AfterValidator(_storable)]
