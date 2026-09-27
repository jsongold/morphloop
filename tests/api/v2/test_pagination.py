"""CursorQuery (#173): default/max limit, opaque cursor round-trip."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness.api.v2.pagination import DEFAULT_LIMIT, CursorQuery, encode_cursor


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
    cursor = encode_cursor("ws_1/highlight_7")
    body = TestClient(_app()).get("/items", params={"cursor": cursor, "limit": 5}).json()
    assert body == {"after": "ws_1/highlight_7", "limit": 5}


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
        "/items", params={"cursor": encode_cursor("ws_1/highlight_7") + "!!!"}
    )
    assert resp.status_code == 400
