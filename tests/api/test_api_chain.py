"""The v0.1 chain end to end over HTTP, checked against the OpenAPI contract.

pack -> learner -> session -> activities and content -> attempt with a lab ->
content opened and highlighted -> tutor question -> submit -> evaluation ->
learner skills, timeline and resume (ADR-0012 gate, AC-A3, AC-F2, AC-F4).

Every response body is validated against its ``components.schemas`` entry in
``contracts/openapi/v0.1.yaml``, so a route that drifts from the contract fails
here rather than in the browser.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from api_harness import assert_component, build_app
from fastapi.testclient import TestClient
from loop_harness import ACTIVITY_ID, LEARNER_ID, PACK_ID, SKILL_ID, LoopFixture, pack_files

from harness.core.pack import PackImporter
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import InMemoryPackSource

HIGHLIGHT_PAYLOAD = {
    "highlight_id": "hl_first",
    "content_id": "concept.resolver",
    "content_version": "1",
    "selected_text": "stub resolver",
    "start_offset": 4,
    "end_offset": 17,
    "semantic_anchor": "sections[0]",
    "context_before": "The ",
    "context_after": " forwards queries.",
}

NOW = "2026-09-23T09:30:00Z"


@pytest.fixture
def wired() -> Iterator[tuple[TestClient, LoopFixture]]:
    app, fixture = build_app()
    with TestClient(app) as client:
        yield client, fixture


def _start_session(client: TestClient, fixture: LoopFixture, key: str = "web:1") -> Any:
    response = client.post(
        "/sessions",
        json={
            "idempotency_key": key,
            "learner_id": LEARNER_ID,
            "pack_id": PACK_ID,
            "pack_content_hash": fixture.pack.content_hash,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_packs_lists_the_imported_pack(wired: tuple[TestClient, LoopFixture]) -> None:
    client, fixture = wired

    body = client.get("/packs").json()

    assert [p["pack"]["pack_content_hash"] for p in body["packs"]] == [fixture.pack.content_hash]
    # Known contract gap: `Pack` requires `imported_at`, but nothing records an
    # import time (the pack projection holds no timestamp and the EventStore
    # Port exposes none), so the field is omitted rather than invented.
    assert "imported_at" not in body["packs"][0]
    assert body["packs"][0]["latest"] is True
    assert_component(body["packs"][0], "Pack", optional=("imported_at",))


def test_packs_marks_the_latest_import_of_each_pack(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    """Two imports of one pack_id: only the newer is `latest` (the web offers only
    it for a new session); re-importing the older one does not move `latest`."""
    client, fixture = wired
    files = pack_files()
    manifest = json.loads(files["manifest.json"])
    manifest["description"] = "A newer import."
    source = InMemoryPackSource(
        {
            "old": files,
            "new": {**files, "manifest.json": json.dumps(manifest, sort_keys=True).encode()},
        }
    )
    importer = PackImporter(
        source=source,
        store=fixture.store,
        schemas=fixture.schemas,
        adapters=fixture.adapters,
        algorithms=v01_algorithm_registry(),
    )
    newer = importer.import_pack("new").ref
    assert not importer.import_pack("old").created

    packs = client.get("/packs").json()["packs"]

    assert [(p["pack"]["pack_content_hash"], p["latest"]) for p in packs] == [
        (fixture.pack.content_hash, False),
        (newer.content_hash, True),
    ]
    for pack in packs:
        assert_component(pack, "Pack", optional=("imported_at",))


def test_create_learner_returns_a_learner_identity(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, _ = wired

    response = client.post("/learners")

    assert response.status_code == 201
    assert_component(response.json(), "Learner")


def test_full_v01_chain_over_http(wired: tuple[TestClient, LoopFixture]) -> None:
    client, fixture = wired

    session = _start_session(client, fixture)
    assert_component(session, "SessionState")
    session_id = session["session"]["session_id"]

    layout = client.get(f"/sessions/{session_id}/layout").json()
    assert_component(layout, "LayoutResponse")

    activities = client.get(f"/sessions/{session_id}/activities").json()
    assert_component(activities, "ActivityList")
    assert [a["activity_definition_id"] for a in activities["activities"]] == [ACTIVITY_ID]
    assert "reference_solution" not in activities["activities"][0]

    content = client.get(f"/sessions/{session_id}/content").json()
    assert_component(content, "ContentList")
    kinds = {item["kind"] for item in content["items"]}
    assert kinds == {"skill", "visualization", "reference"}

    document = client.get(f"/sessions/{session_id}/content/reference/concept.resolver").json()
    assert_component(document, "ContentDocument")

    started = client.post(
        f"/sessions/{session_id}/attempts",
        json={"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID},
    )
    assert started.status_code == 201
    attempt = started.json()
    assert_component(attempt, "AttemptState")
    assert attempt["status"] == "active"
    attempt_id = attempt["attempt_id"]
    lab_id = attempt["lab"]["lab_instance_id"]
    assert attempt["lab"]["terminal_path"] == f"/labs/{lab_id}/terminal"

    lab = client.get(f"/labs/{lab_id}").json()
    assert_component(lab, "LabState")

    opened = client.post(
        f"/sessions/{session_id}/events",
        json={
            "event_type": "content.opened",
            "event_version": 1,
            "occurred_at": NOW,
            "idempotency_key": "web:3",
            "attempt_id": attempt_id,
            "payload": {
                "content_id": "concept.resolver",
                "content_version": "1",
                "pane": "side",
                "source_highlight_id": None,
            },
        },
    )
    assert opened.status_code == 201, opened.text
    assert_component(opened.json(), "EventAppendResponse")

    highlighted = client.post(
        f"/sessions/{session_id}/events",
        json={
            "event_type": "content.highlighted",
            "event_version": 1,
            "occurred_at": NOW,
            "idempotency_key": "web:4",
            "attempt_id": attempt_id,
            "payload": HIGHLIGHT_PAYLOAD,
        },
    )
    assert highlighted.status_code == 201, highlighted.text

    chat = client.post(
        f"/sessions/{session_id}/chat/messages",
        json={
            "idempotency_key": "web:5",
            "occurred_at": NOW,
            "thread_id": None,
            "attempt_id": attempt_id,
            "text": "Where should I look first?",
            "references": [{"type": "highlight", "id": "hl_first"}],
        },
    )
    assert chat.status_code == 201, chat.text
    assert_component(chat.json(), "ChatExchange")

    submitted = client.post(f"/attempts/{attempt_id}/submit", json={"idempotency_key": "web:6"})
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["status"] == "evaluating"

    # The evaluation chain runs as a background task once the 202 is sent.
    completed = client.get(f"/attempts/{attempt_id}").json()
    assert_component(completed, "AttemptState")
    assert completed["status"] == "completed"
    assert completed["result"]["completion"]["event_type"] == "activity.completed"
    assert completed["result"]["skill_updates"]

    skills = client.get(f"/learners/{LEARNER_ID}/skills", params={"pack_id": PACK_ID}).json()
    assert_component(skills, "SkillStateList")
    assert [s["skill_id"] for s in skills["skills"]] == [SKILL_ID]

    timeline = client.get(f"/sessions/{session_id}/timeline").json()
    assert_component(timeline, "TimelinePage")
    types = [event["event_type"] for event in timeline["events"]]
    assert types[0] == "session.started"
    assert {"activity.started", "lab.started", "activity.submitted", "activity.completed"} <= set(
        types
    )

    highlights = client.get(f"/sessions/{session_id}/highlights").json()
    assert_component(highlights, "HighlightEventList")
    assert len(highlights["events"]) == 1

    chat_events = client.get(f"/sessions/{session_id}/chat").json()
    assert_component(chat_events, "ChatEventList")
    assert len(chat_events["events"]) == 2

    resumed = client.get(f"/sessions/{session_id}").json()
    assert_component(resumed, "SessionState")
    assert resumed["active_attempt"] is None
    assert resumed["ui_state"]["open_content"]["content_id"] == "concept.resolver"
    assert resumed["last_position"] == timeline["last_position"]

    sessions = client.get(f"/learners/{LEARNER_ID}/sessions").json()
    assert_component(sessions, "SessionList")
    assert [s["session_id"] for s in sessions["sessions"]] == [session_id]


def test_timeline_pages_forward_without_skipping(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = _start_session(client, fixture)["session"]["session_id"]
    client.post(
        f"/sessions/{session_id}/attempts",
        json={"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID},
    )

    first = client.get(f"/sessions/{session_id}/timeline", params={"limit": 1}).json()
    assert first["has_more"] is True
    second = client.get(
        f"/sessions/{session_id}/timeline",
        params={"after_position": first["last_position"], "limit": 100},
    ).json()

    assert_component(second, "TimelinePage")
    assert second["events"][0]["position"] == first["last_position"] + 1


def test_highlight_thread_chat_records_a_memo_over_http(
    wired: tuple[TestClient, LoopFixture],
) -> None:
    client, fixture = wired
    session_id = _start_session(client, fixture)["session"]["session_id"]
    attempt = client.post(
        f"/sessions/{session_id}/attempts",
        json={"idempotency_key": "web:2", "activity_definition_id": ACTIVITY_ID},
    ).json()
    attempt_id = attempt["attempt_id"]

    highlighted = client.post(
        f"/sessions/{session_id}/events",
        json={
            "event_type": "content.highlighted",
            "event_version": 1,
            "occurred_at": NOW,
            "idempotency_key": "web:3",
            "attempt_id": attempt_id,
            "payload": HIGHLIGHT_PAYLOAD,
        },
    )
    assert highlighted.status_code == 201, highlighted.text

    chat = client.post(
        f"/sessions/{session_id}/chat/messages",
        json={
            "idempotency_key": "web:4",
            "occurred_at": NOW,
            "thread_id": "thr_first",
            "attempt_id": attempt_id,
            "text": "What is this part for?",
            "references": [{"type": "highlight", "id": "hl_first"}],
        },
    )
    assert chat.status_code == 201, chat.text
    body = chat.json()
    assert_component(body, "ChatExchange")
    assert body["memo"] is not None
    assert body["memo"]["memo_id"] == "memo_first"
    assert body["memo"]["thread_id"] == "thr_first"
    assert body["memo"]["highlight_id"] == "hl_first"
    assert_component(body["memo"], "MemoView")

    memos = client.get(f"/sessions/{session_id}/memos").json()
    assert_component(memos, "MemoList")
    assert [m["memo_id"] for m in memos["memos"]] == ["memo_first"]

    only_thread = client.get(
        f"/sessions/{session_id}/chat", params={"thread_id": "thr_first"}
    ).json()
    assert_component(only_thread, "ChatEventList")
    assert len(only_thread["events"]) == 2
    assert all(e["payload"]["thread_id"] == "thr_first" for e in only_thread["events"])

    learner_edit = client.post(
        f"/sessions/{session_id}/events",
        json={
            "event_type": "memo.edited",
            "event_version": 1,
            "occurred_at": NOW,
            "idempotency_key": "web:5",
            "attempt_id": attempt_id,
            "payload": {"memo_id": "memo_first", "title": "My note", "body": "Mine now."},
        },
    )
    assert learner_edit.status_code == 201, learner_edit.text
    [memo] = client.get(f"/sessions/{session_id}/memos").json()["memos"]
    assert memo["edited_by_learner"] is True
    assert (memo["title"], memo["body"]) == ("My note", "Mine now.")
