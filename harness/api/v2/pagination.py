"""Cursor query parameters for `/v2` list routes (#173).

Wraps the `ViewDocumentStore`/`View.list` keyset cursor (a raw view document
key) as an opaque string over the wire, so a client never sees or constructs
a key itself: `CursorQuery` decodes the incoming `cursor` query parameter to
the `after` value `View.list(..., after=..., limit=...)` expects, and
`encode_cursor` turns a returned `next_cursor` back into the string a client
echoes back as its next `cursor`.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(key: str) -> str:
    """The opaque cursor for a view document key."""
    return base64.urlsafe_b64encode(key.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(422, "cursor is not a recognized page cursor") from exc


@dataclass(frozen=True, slots=True)
class CursorPage:
    """Decoded page request: pass straight through to `View.list`/`list_view`."""

    after: str | None
    limit: int


def cursor_page_of(
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CursorPage:
    return CursorPage(after=_decode_cursor(cursor) if cursor else None, limit=limit)


CursorQuery = Annotated[CursorPage, Depends(cursor_page_of)]
