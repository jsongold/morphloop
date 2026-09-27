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
MAX_LIMIT = 100  # contracts/openapi/v0.2/components/common.yaml LimitParam


def encode_cursor(key: str, collection: str) -> str:
    """The opaque cursor for a view document key of ``collection``.

    The collection name is baked in so a cursor from one list route (e.g. a
    ws's threads) is refused by another whose keys share the same prefix
    (e.g. the same ws's memo entries) -- see `CursorPage.after_in` (#236).
    """
    return base64.urlsafe_b64encode(f"{collection}:{key}".encode()).decode()


def _decode_cursor(cursor: str) -> str:
    # urlsafe_b64decode has no `validate` param, so translate the urlsafe
    # alphabet (-_ -> +/) ourselves and call b64decode(validate=True), which
    # rejects non-alphabet characters instead of silently discarding them.
    try:
        translated = cursor.encode().translate(bytes.maketrans(b"-_", b"+/"))
        return base64.b64decode(translated, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        # contracts/openapi/v0.2/components/common.yaml CursorParam: unknown
        # or malformed cursor -> 400 `invalid-request`, not 422.
        raise HTTPException(400, "cursor is not a recognized page cursor") from exc


@dataclass(frozen=True, slots=True)
class CursorPage:
    """Decoded page request; `after_in` yields the `View.list(after=...)` key."""

    after: str | None
    limit: int

    def after_in(self, prefix: str, collection: str) -> str | None:
        """The view key in ``self.after``, checked against the request's own
        ``collection`` (the name given to `encode_cursor`) and
        ``View.list(key_prefix=...)``.

        A syntactically valid cursor (right base64/UTF-8) can still be one
        never issued for this resource, or issued for another ws/user -- its
        decoded key then falls outside ``prefix`` and the store would just
        page from wherever it happens to sort, silently. Per
        `contracts/openapi/v0.2/components/common.yaml` `CursorParam`
        ("Unknown or expired -> 400 `invalid-request`"), that is a 400, not a
        200 with a wrong/empty page (#221, #226). Two collections can share a
        key prefix (a ws's threads and memo entries are both ``<ws_id>/...``),
        so the prefix alone is not enough: the cursor must also carry this
        collection's name (#236, #256).
        """
        if self.after is None:
            return None
        tag = f"{collection}:"
        if not self.after.startswith(tag + prefix):
            raise HTTPException(400, "cursor does not belong to this resource")
        return self.after.removeprefix(tag)


def cursor_page_of(
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CursorPage:
    return CursorPage(after=_decode_cursor(cursor) if cursor else None, limit=limit)


CursorQuery = Annotated[CursorPage, Depends(cursor_page_of)]
