"""The v0.1 gate chain against the *real* stack (ADR-0012, ACCEPTANCE_CRITERIA).

``tests/api`` drives the same chain over fakes; this one drives it over HTTP and
WebSocket against ``docker compose up`` -- a real Postgres event store, a real
Docker lab, a real PTY and real LLM calls -- so it is the check that the slice
works, not only that the wiring type-checks::

    docker compose up -d --build              # README Quick Start
    docker compose exec -T api python -m harness.cli import contents/software-engineering
    set -a; . ./.env.local; set +a            # the LLM key litellm reads
    uv run pytest tests/e2e -q

It is skipped unless all of that is in place: the Docker CLI, the API answering
``/health`` with a database, an imported pack, and an LLM key.
``MORPHLOOP_API_URL`` overrides the base URL (use it when ``API_PORT`` is not
8000).

The rebuild step (AC-F6) runs ``python -m harness.cli rebuild`` inside the api
container rather than in-process: the shipped ``docker-compose.yml`` does not
publish the database port, so ``DATABASE_URL`` is only reachable from there.
Override with ``MORPHLOOP_REBUILD_CMD`` for another deployment.

Response bodies are validated against ``contracts/openapi/v0.1.yaml`` and
WebSocket frames against ``contracts/schemas/ws/``, with the same helpers the
fake-driven API tests use.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.sync.client import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
# api_harness owns the OpenAPI/ws validators; pytest only puts a test directory
# on sys.path once it collects from it, so importing it needs the path itself.
sys.path.insert(0, str(REPO_ROOT / "tests" / "api"))

from api_harness import assert_component, assert_ws_message  # noqa: E402

API_URL = os.environ.get("MORPHLOOP_API_URL", "http://localhost:8000")
REBUILD_CMD = os.environ.get(
    "MORPHLOOP_REBUILD_CMD", "docker compose exec -T api python -m harness.cli rebuild"
)
PACK_ID = "software-engineering"
ACTIVITY_ID = "gen-diagnose-dns-resolver-misconfiguration-001"
SKILL_ID = "network.dns.resolution"
REFERENCE_ID = "network.dns.resolver"

SERVICE = "ledger.corp.internal"
SERVICE_ADDRESS = "127.0.0.42"
BROKEN_NAMESERVER = "203.0.113.77"
GOOD_NAMESERVER = "127.0.0.53"
# What the learner types to repair the resolver. Deliberately written out here
# rather than read from the pack: the reference solution is withheld (AC-J6).
FIX_COMMAND = (
    f"printf 'nameserver {GOOD_NAMESERVER}\\noptions timeout:1 attempts:1\\n' > /etc/resolv.conf"
)

PROTOCOL = 1
EVALUATION_TIMEOUT = 300.0
TERMINAL_TIMEOUT = 60.0


def _why_skipped() -> str | None:
    if subprocess.run(["docker", "version"], capture_output=True).returncode != 0:  # noqa: S603,S607
        return "the Docker CLI cannot reach a daemon"
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        return "no LLM key in the environment (OPENAI_API_KEY / ANTHROPIC_API_KEY)"
    try:
        health = httpx.get(f"{API_URL}/health", timeout=5.0).json()
        packs = httpx.get(f"{API_URL}/packs", timeout=5.0).json()["packs"]
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return f"no API at {API_URL} ({error})"
    if health.get("db") != "ok":
        return f"the API at {API_URL} has no database"
    if not any(view["pack"]["pack_id"] == PACK_ID for view in packs):
        return f"{PACK_ID} is not imported (python -m harness.cli import contents/{PACK_ID})"
    return None


_SKIP = _why_skipped()
pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=API_URL, timeout=EVALUATION_TIMEOUT) as client:
        yield client


def _post(api: httpx.Client, path: str, body: dict[str, Any], expect: int) -> httpx.Response:
    response = api.post(path, json=body)
    assert response.status_code == expect, f"POST {path}: {response.status_code} {response.text}"
    return response


def _get(api: httpx.Client, path: str, **params: Any) -> Any:
    response = api.get(path, params=params or None)
    assert response.status_code == 200, f"GET {path}: {response.status_code} {response.text}"
    return response.json()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _frame(message: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": message,
            "protocol_version": PROTOCOL,
            "correlation_id": None,
            "idempotency_key": None,
            "payload": payload,
        }
    )


class Terminal:
    """The lab terminal WebSocket, driven the way a learner types into xterm.js."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    def wait_ready(self) -> str:
        """Drain until the ``lab.status`` that carries the terminal id (ws/README.md)."""
        deadline = time.monotonic() + TERMINAL_TIMEOUT
        while time.monotonic() < deadline:
            message = self._receive()
            if message["type"] == "lab.status" and message["payload"]["terminal_id"] is not None:
                assert message["payload"]["status"] == "ready"
                terminal_id: str = message["payload"]["terminal_id"]
                return terminal_id
        raise AssertionError("the lab terminal never became ready")

    def run(self, command: str, *, expect: str = "") -> str:
        """Type one command; collect output until the shell reports its status.

        The command is followed by an ``echo`` of a per-command marker and
        ``$?``. The echoed input line contains the marker with a literal
        ``$?`` after it, so only the shell's own line matches ``marker<digit>``.
        """
        marker = f"__e2e{abs(hash(command)) % 10**8}__"
        done = re.compile(re.escape(marker) + r"\d")
        self._ws.send(_frame("terminal.input", {"data": f"{command}; echo {marker}$?\n"}))
        seen = ""
        deadline = time.monotonic() + TERMINAL_TIMEOUT
        while time.monotonic() < deadline:
            message = self._receive()
            if message["type"] != "terminal.output":
                continue
            seen += message["payload"]["data"]
            if done.search(seen):
                if expect:
                    assert expect in seen, f"{command!r} did not show {expect!r}:\n{seen}"
                return seen
        raise AssertionError(f"{command!r} did not finish within {TERMINAL_TIMEOUT}s:\n{seen}")

    def _receive(self) -> dict[str, Any]:
        message: dict[str, Any] = json.loads(self._ws.recv(timeout=TERMINAL_TIMEOUT))
        assert_ws_message(message)
        return message


def _terminal_url(lab_id: str) -> str:
    return f"{API_URL.replace('http', 'ws', 1)}/labs/{lab_id}/terminal"


def _poll_attempt(api: httpx.Client, attempt_id: str) -> Any:
    """The attempt once evaluation (checks, evaluator, learner model) finishes."""
    deadline = time.monotonic() + EVALUATION_TIMEOUT
    while time.monotonic() < deadline:
        attempt = _get(api, f"/attempts/{attempt_id}")
        if attempt["status"] != "evaluating":
            return attempt
        time.sleep(2.0)
    raise AssertionError(f"{attempt_id} was still evaluating after {EVALUATION_TIMEOUT}s")


def test_v01_chain_on_the_real_stack(api: httpx.Client) -> None:
    run = f"e2e:{time.time_ns()}"

    # --- pack, learner, session (AC-G2) -------------------------------------
    packs = _get(api, "/packs")["packs"]
    pack = next(view for view in packs if view["pack"]["pack_id"] == PACK_ID)
    assert_component(pack, "Pack", optional=("imported_at",))
    content_hash = pack["pack"]["pack_content_hash"]

    learner = _post(api, "/learners", {}, 201).json()
    assert_component(learner, "Learner")
    learner_id = learner["learner_id"]

    session = _post(
        api,
        "/sessions",
        {
            "idempotency_key": f"{run}:session",
            "learner_id": learner_id,
            "pack_id": PACK_ID,
            "pack_content_hash": content_hash,
        },
        201,
    ).json()
    assert_component(session, "SessionState")
    session_id = session["session"]["session_id"]

    layout = _get(api, f"/sessions/{session_id}/layout")
    assert_component(layout, "LayoutResponse")
    assert layout["layout"]["layout"]["main"]["modes"], "the layout comes from the pack (ADR-0003)"

    activities = _get(api, f"/sessions/{session_id}/activities")
    assert_component(activities, "ActivityList")
    activity = next(
        item for item in activities["activities"] if item["activity_definition_id"] == ACTIVITY_ID
    )
    assert "reference_solution" not in activity, "the solution is withheld (AC-J6)"

    # --- the DNS visualization and its reality mapping (AC-C1, AC-C2) -------
    content = _get(api, f"/sessions/{session_id}/content")
    assert_component(content, "ContentList")
    assert {"skill", "visualization", "reference"} <= {item["kind"] for item in content["items"]}
    visualization = _get(api, f"/sessions/{session_id}/content/visualization/dns-resolution-flow")
    assert_component(visualization, "ContentDocument")
    steps = visualization["document"]["diagram"]["steps"]
    # A step's reality mapping is optional in the pack schema (the recursion
    # step has none: a loopback-only lab cannot show it), but every mapping that
    # is there names a mechanism and at least one runnable command (AC-C2).
    mapped = [step for step in steps if "reality" in step]
    assert mapped, "the DNS visualization has reality mappings (AC-C1, AC-C2)"
    assert all(step["reality"]["mechanism"] and step["reality"]["observe"] for step in mapped), [
        step["id"] for step in mapped
    ]
    observe = next(
        item
        for step in mapped
        for item in step["reality"]["observe"]
        if item["argv"] == ["cat", "/etc/resolv.conf"]
    )
    assert observe["look_for"]

    # --- attempt with a real Docker lab (AC-B1, AC-B5, AC-J3) ---------------
    attempt = _post(
        api,
        f"/sessions/{session_id}/attempts",
        {"idempotency_key": f"{run}:attempt", "activity_definition_id": ACTIVITY_ID},
        201,
    ).json()
    assert_component(attempt, "AttemptState")
    assert attempt["status"] == "active"
    attempt_id, lab_id = attempt["attempt_id"], attempt["lab"]["lab_instance_id"]
    definition_hash = attempt["activity"]["activity_definition_hash"]
    assert_component(_get(api, f"/labs/{lab_id}"), "LabState")

    # Selecting the visualization step is what exposes its reality mapping (AC-C2).
    _post(
        api,
        f"/sessions/{session_id}/events",
        {
            "event_type": "visualization.step_selected",
            "event_version": 1,
            "occurred_at": _now(),
            "idempotency_key": f"{run}:step",
            "attempt_id": attempt_id,
            "payload": {
                "visualization_id": "dns-resolution-flow",
                "content_version": visualization["content_version"],
                "step_id": "dns-query",
            },
        },
        201,
    )

    with connect(_terminal_url(lab_id), open_timeout=TERMINAL_TIMEOUT) as ws:
        terminal = Terminal(ws)
        terminal.wait_ready()
        # Diagnose. The first command is the visualization step's own observe
        # argv, run verbatim in the lab without leaving the activity (AC-C3).
        terminal.run(shlex.join(observe["argv"]), expect=BROKEN_NAMESERVER)
        terminal.run(f"getent ahosts {SERVICE} || echo LOOKUP_FAILED", expect="LOOKUP_FAILED")
        terminal.run(f"dig +short @{GOOD_NAMESERVER} {SERVICE}", expect=SERVICE_ADDRESS)
        # The lab cannot reach the host's Docker socket or filesystem (AC-B5).
        terminal.run(
            "test -e /var/run/docker.sock && echo HOST_DOCKER || echo NO_HOST_DOCKER",
            expect="NO_HOST_DOCKER",
        )

    # --- the commands are evidence (AC-B2, AC-B3) ---------------------------
    commands = [
        event
        for event in _get(api, f"/sessions/{session_id}/timeline", limit=500)["events"]
        if event["event_type"] == "terminal.command"
    ]
    assert commands, "terminal commands are persisted"
    assert all(event["attempt_id"] == attempt_id for event in commands)
    assert all(event["activity_definition_id"] == ACTIVITY_ID for event in commands)
    assert all(event["occurred_at"] for event in commands)
    outputs = [
        event
        for event in _get(api, f"/sessions/{session_id}/timeline", limit=500)["events"]
        if event["event_type"] == "terminal.output"
    ]
    assert outputs, "terminal output is persisted for the evaluator (AC-B3)"

    # --- side pane, highlight, quoted question (AC-D1..D5, AC-E1..E3) -------
    _post(
        api,
        f"/sessions/{session_id}/events",
        {
            "event_type": "content.opened",
            "event_version": 1,
            "occurred_at": _now(),
            "idempotency_key": f"{run}:opened",
            "attempt_id": attempt_id,
            "payload": {
                "content_id": REFERENCE_ID,
                "content_version": "1",
                "pane": "side",
                "source_highlight_id": None,
            },
        },
        201,
    )
    highlight_id = f"hl_{time.time_ns():x}"
    highlighted = _post(
        api,
        f"/sessions/{session_id}/events",
        {
            "event_type": "content.highlighted",
            "event_version": 1,
            "occurred_at": _now(),
            "idempotency_key": f"{run}:highlight",
            "attempt_id": attempt_id,
            "payload": {
                "highlight_id": highlight_id,
                "content_id": REFERENCE_ID,
                "content_version": "1",
                "selected_text": "stub resolver",
                "start_offset": 0,
                "end_offset": 13,
                "semantic_anchor": "sections[0]",
                "context_before": "",
                "context_after": "",
            },
        },
        201,
    ).json()
    assert_component(highlighted, "EventAppendResponse")

    chat = _post(
        api,
        f"/sessions/{session_id}/chat/messages",
        {
            "idempotency_key": f"{run}:chat",
            "occurred_at": _now(),
            "thread_id": None,
            "attempt_id": attempt_id,
            "text": "What does this mean, and what should I check next in my lab?",
            "references": [{"type": "highlight", "id": highlight_id}],
        },
        201,
    ).json()
    assert_component(chat, "ChatExchange")
    # The question carries the quote the UI renders (AC-D3, AC-D4).
    assert chat["request"]["payload"]["references"] == [{"type": "highlight", "id": highlight_id}]
    reply = chat["reply"]["payload"]
    # The tutor knows the mission and answers in the pack's unfinished-attempt
    # default mode (AC-E1, AC-E3); the reply cites the highlight (AC-D5).
    assert reply["mode"] == "hint"
    assert highlight_id in json.dumps(reply["references"]), reply["references"]
    assert reply["text"].strip()

    # --- reset restores the fixture (AC-B4) ---------------------------------
    reset = _post(api, f"/labs/{lab_id}/reset", {"idempotency_key": f"{run}:reset"}, 202).json()
    assert_component(reset, "LabState")
    fresh_lab_id = reset["lab_instance_id"]
    assert fresh_lab_id != lab_id

    # --- fix the fault in the fresh lab and submit (AC-C3) ------------------
    with connect(_terminal_url(fresh_lab_id), open_timeout=TERMINAL_TIMEOUT) as ws:
        terminal = Terminal(ws)
        terminal.wait_ready()
        terminal.run("cat /etc/resolv.conf", expect=BROKEN_NAMESERVER)
        terminal.run(FIX_COMMAND)
        terminal.run(f"getent ahosts {SERVICE}", expect=SERVICE_ADDRESS)
        terminal.run(
            f"curl -s -o /dev/null -w '%{{http_code}}\\n' http://{SERVICE}:9093/readyz",
            expect="200",
        )

    submitted = _post(
        api, f"/attempts/{attempt_id}/submit", {"idempotency_key": f"{run}:submit"}, 202
    )
    assert submitted.json()["status"] == "evaluating"
    assert submitted.headers.get("Idempotent-Replayed") is None

    # --- checks -> evidence -> evaluation -> skill update (AC-A3, AC-E4/E5) -
    completed = _poll_attempt(api, attempt_id)
    assert_component(completed, "AttemptState")
    assert completed["status"] == "completed", completed.get("last_submission_error")
    assert completed["activity"]["activity_definition_hash"] == definition_hash, "AC-J3"
    result = completed["result"]
    assert result["outcome"] == "passed", result
    checks = result["evaluation"]["payload"]["checks"]
    assert checks and all(check["passed"] for check in checks), checks
    evidence_ids = {event["payload"]["evidence_id"] for event in result["evidence"]}
    assert evidence_ids, "the evaluator produced evidence"
    assert result["skill_updates"], "the learner model updated a skill"
    for update in result["skill_updates"]:
        cited = set(update["payload"]["evidence_ids"])
        assert cited and cited <= evidence_ids, (cited, evidence_ids)
        assert update["payload"]["next"]["mastery_probability"] is not None

    skills = _get(api, f"/learners/{learner_id}/skills", pack_id=PACK_ID)
    assert_component(skills, "SkillStateList")
    assert [skill["skill_id"] for skill in skills["skills"]] == [SKILL_ID]

    # --- timeline: append-only, position-ordered (AC-F1, AC-F2, AC-F7) ------
    timeline = _get(api, f"/sessions/{session_id}/timeline", limit=500)
    assert_component(timeline, "TimelinePage")
    positions = [event["position"] for event in timeline["events"]]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    types = [event["event_type"] for event in timeline["events"]]
    assert types[0] == "session.started"
    assert {
        "activity.started", "lab.started", "terminal.command", "terminal.output",
        "content.opened", "content.highlighted", "assistant.message_requested",
        "assistant.message_generated", "lab.reset", "activity.submitted",
        "evaluation.completed", "evidence.created", "learner_skill.updated",
        "activity.completed",
    } <= set(types)  # fmt: skip

    # Provenance travels with the events (AC-H3).
    provenance = timeline["events"][0]["payload"]["provenance"]
    assert provenance["pack"]["pack_id"] == PACK_ID
    assert provenance["pack"]["pack_content_hash"] == content_hash
    assert provenance["harness_version"] and provenance["domain_adapters"]["dns"]
    assert provenance["registry_implementations"]["learner_model"].startswith("llm-")

    # --- resending the same key changes nothing (AC-F5) ---------------------
    replay = _post(api, f"/attempts/{attempt_id}/submit", {"idempotency_key": f"{run}:submit"}, 202)
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert (
        _get(api, f"/sessions/{session_id}/timeline", limit=500)["last_position"]
        == timeline["last_position"]
    ), "a replayed submit appended no event (AC-F1, AC-F5)"

    # --- resume restores session, chat and highlights (AC-F4, AC-D2) --------
    resumed = _get(api, f"/sessions/{session_id}")
    assert_component(resumed, "SessionState")
    assert resumed["ui_state"]["open_content"]["content_id"] == REFERENCE_ID
    assert resumed["last_position"] == timeline["last_position"]
    highlights = _get(api, f"/sessions/{session_id}/highlights")
    assert_component(highlights, "HighlightEventList")
    assert [e["payload"]["highlight_id"] for e in highlights["events"]] == [highlight_id]
    chat_events = _get(api, f"/sessions/{session_id}/chat")
    assert_component(chat_events, "ChatEventList")
    assert len(chat_events["events"]) == 2
    sessions = _get(api, f"/learners/{learner_id}/sessions")["sessions"]
    assert [item["session_id"] for item in sessions] == [session_id]

    # --- rebuild yields the same projections (AC-F6) ------------------------
    # `AttemptState.lab` is read live from Docker rather than from a projection,
    # so the comparison uses the stored evaluation chain instead of the whole
    # attempt; everything else is projection state.
    before = (resumed, skills, completed["result"])
    rebuilt = subprocess.run(  # noqa: S603
        shlex.split(REBUILD_CMD), cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert "replayed" in rebuilt.stdout, rebuilt.stdout
    after = (
        _get(api, f"/sessions/{session_id}"),
        _get(api, f"/learners/{learner_id}/skills", pack_id=PACK_ID),
        _get(api, f"/attempts/{attempt_id}")["result"],
    )
    assert before == after, "rebuilding the projections changed the state"
