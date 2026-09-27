"""CursorQuery (#173): default/max limit, opaque cursor round-trip."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from harness.api.v2.pagination import DEFAULT_LIMIT, CursorPage, CursorQuery, encode_cursor


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/items")
    def items(page: CursorQuery) -> dict[str, Any]:
        return {"after": page.after, "limit": page.limit}

    return app


def test_default_limit_and_no_cursor() -> None:
    body = TestClient(_app()).get("/items").json()
    assert body == {"after": None, "limit": DEFAULT_LIMIT}


def test_cursor_round_trips_through_encode_and_the_query_param() -> None:
    cursor = encode_cursor("ws_1/highlight_7", "items")
    body = TestClient(_app()).get("/items", params={"cursor": cursor, "limit": 5}).json()
    assert body == {"after": "items:ws_1/highlight_7", "limit": 5}
    assert CursorPage(after=body["after"], limit=5).after_in("ws_1/", "items") == "ws_1/highlight_7"


def test_limit_over_the_max_is_rejected() -> None:
    from harness.api.v2.pagination import MAX_LIMIT

    resp = TestClient(_app()).get("/items", params={"limit": MAX_LIMIT + 1})
    assert resp.status_code == 422


def test_malformed_cursor_is_rejected() -> None:
    # 400 invalid-request per contracts/openapi/v0.2/components/common.yaml
    # CursorParam ("Unknown or expired -> 400 `invalid-request`"); the bare
    # FastAPI app here has no Problem handlers installed, so the raw
    # HTTPException status is what surfaces.
    resp = TestClient(_app()).get("/items", params={"cursor": "not-base64!"})
    assert resp.status_code == 400


def test_cursor_of_only_invalid_characters_is_rejected() -> None:
    """#208: a correctly-padded but non-alphabet cursor must be rejected, not
    silently decode to an empty/garbage key."""
    for bad in ("!!!!", "%%%"):
        resp = TestClient(_app()).get("/items", params={"cursor": bad})
        assert resp.status_code == 400, bad


def test_valid_cursor_with_garbage_appended_is_rejected() -> None:
    resp = TestClient(_app()).get(
        "/items", params={"cursor": encode_cursor("ws_1/highlight_7", "items") + "!!!"}
    )
    assert resp.status_code == 400


def test_after_in_accepts_a_cursor_matching_the_prefix() -> None:
    page = CursorPage(after="highlights:ws_1:hl_7", limit=10)
    assert page.after_in("ws_1:", "highlights") == "ws_1:hl_7"


def test_after_in_accepts_no_cursor() -> None:
    assert CursorPage(after=None, limit=10).after_in("ws_1:", "highlights") is None


def test_after_in_rejects_a_cursor_for_another_resource() -> None:
    # #221/#226: a syntactically valid cursor issued for a different
    # ws/user must not silently page a foreign key range.
    page = CursorPage(after="highlights:ws_2:hl_9", limit=10)
    with pytest.raises(HTTPException) as excinfo:
        page.after_in("ws_1:", "highlights")
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize("after", ["threads:ws_1/000001", "ws_1/000001", "memo-entriesX:ws_1/1"])
def test_after_in_rejects_a_cursor_for_another_collection(after: str) -> None:
    # #236/#256: same ws prefix, different (or no) collection tag.
    with pytest.raises(HTTPException) as excinfo:
        CursorPage(after=after, limit=10).after_in("ws_1/", "memo-entries")
    assert excinfo.value.status_code == 400


def test_after_in_rejects_a_cursor_via_the_route() -> None:
    app = FastAPI()

    @app.get("/ws/{ws_id}/items")
    def items(ws_id: str, page: CursorQuery) -> dict[str, Any]:
        return {"after": page.after_in(f"{ws_id}:", "items")}

    resp = TestClient(app).get(
        "/ws/ws_1/items", params={"cursor": encode_cursor("ws_2:hl_9", "items")}
    )
    assert resp.status_code == 400
