"""Contract tests for contracts/openapi/v0.1.yaml (ADR-0015, ADR-0017).

Checks that the document is valid OpenAPI 3.1, that every `$ref` resolves
(external refs by `$id` to a schema under contracts/, never over the network),
a few structural rules from contracts/openapi/README.md, and that example
request/response bodies validate against the component schemas. Stored-event
examples reuse tests/contracts/fixtures/events/valid/.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema_path import SchemaPath
from jsonschema_path.handlers import default_handlers
from openapi_spec_validator.validation import OpenAPIV31SpecValidator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from harness.testing.contracts import CONTRACTS_DIR

ID_BASE = "https://morphloop.dev/contracts/"
OPENAPI_PATH = CONTRACTS_DIR / "openapi" / "v0.1.yaml"
OPENAPI_URI = ID_BASE + "openapi/v0.1.yaml"
EVENT_FIXTURES = Path(__file__).parent / "fixtures" / "events" / "valid"

# POST operations that append no event and therefore take no idempotency_key.
NON_EVENT_POSTS = {"/learners"}


def _load_spec() -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    return spec


SPEC = _load_spec()


def _schemas_by_id() -> dict[str, dict[str, Any]]:
    """Every `$id`-bearing JSON document under contracts/, keyed by `$id`."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(CONTRACTS_DIR.rglob("*.json")):
        try:
            contents = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # contracts/schemas/pack/ may be mid-edit; its own tests report it.
            continue
        if isinstance(contents, dict) and "$id" in contents:
            out[contents["$id"]] = contents
    return out


SCHEMAS = _schemas_by_id()


def _local_handler(uri: str) -> Any:
    base = uri.split("#", 1)[0]
    if base not in SCHEMAS:
        raise LookupError(f"no contracts schema with $id {base}")
    return SCHEMAS[base]


def _refs(node: Any, where: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield where, value
            else:
                yield from _refs(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _refs(value, f"{where}[{i}]")


def _pointer(document: Any, fragment: str) -> Any:
    node = document
    if fragment in ("", "/"):
        return node
    for raw in fragment.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def _validator(component: str) -> Draft202012Validator:
    resources: list[tuple[str, Resource[Any]]] = [
        (sid, Resource.from_contents(doc)) for sid, doc in SCHEMAS.items()
    ]
    resources.append((OPENAPI_URI, DRAFT202012.create_resource(SPEC)))
    registry: Registry[Any] = Registry().with_resources(resources)
    return Draft202012Validator(
        {"$ref": f"{OPENAPI_URI}#/components/schemas/{component}"}, registry=registry
    )


def _is_valid(instance: object, component: str) -> bool:
    return _validator(component).is_valid(instance)


def _event(event_type: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (EVENT_FIXTURES / f"{event_type}.v1.json").read_text(encoding="utf-8")
    )
    return data


def _operations() -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in SPEC["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                yield path, method, item[method]


# ---------------------------------------------------------------- document


def test_is_openapi_3_1() -> None:
    assert SPEC["openapi"].startswith("3.1.")


def test_document_is_valid_openapi_3_1() -> None:
    handlers = dict(default_handlers)
    handlers["https"] = _local_handler
    handlers["http"] = _local_handler
    OpenAPIV31SpecValidator(SchemaPath.from_dict(SPEC, handlers=handlers)).validate()


def test_every_external_ref_resolves_to_a_contracts_schema_id() -> None:
    external = [(w, r) for w, r in _refs(SPEC) if not r.startswith("#")]
    assert external, "expected refs into contracts/schemas"
    for where, ref in external:
        assert ref.startswith(ID_BASE + "schemas/"), f"{where}: {ref} is not a contracts $id"
        assert not ref.startswith(ID_BASE + "schemas/pack/"), f"{where}: pack schemas not frozen"
        base, _, fragment = ref.partition("#")
        assert base in SCHEMAS, f"{where}: no schema with $id {base}"
        try:
            _pointer(SCHEMAS[base], fragment)
        except (KeyError, IndexError, ValueError) as exc:
            raise AssertionError(f"{where}: fragment #{fragment} not found in {base}") from exc


def test_every_internal_ref_resolves() -> None:
    for where, ref in _refs(SPEC):
        if ref.startswith("#"):
            try:
                _pointer(SPEC, ref[1:])
            except (KeyError, IndexError, ValueError) as exc:
                raise AssertionError(f"{where}: {ref} does not resolve") from exc


def test_websocket_terminal_is_documented_with_ws_message_schema() -> None:
    ws = SPEC["x-websockets"]["/labs/{lab_instance_id}/terminal"]
    assert ws["message"]["$ref"] == ID_BASE + "schemas/ws/envelope/message.json"
    ws_types = set(SCHEMAS[ID_BASE + "schemas/ws/dispatch.json"]["properties"]["type"]["enum"])
    assert set(ws["clientToServer"]) | set(ws["serverToClient"]) == ws_types


def test_health_endpoint_matches_slice0() -> None:
    assert "get" in SPEC["paths"]["/health"]
    assert _is_valid({"status": "ok", "db": "down"}, "Health")


def test_every_event_appending_post_requires_idempotency_key() -> None:
    for path, method, op in _operations():
        if method != "post" or path in NON_EVENT_POSTS:
            continue
        ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema = _pointer(SPEC, ref[1:])
        assert "idempotency_key" in schema["required"], f"POST {path}"
        assert "Idempotent-Replayed" in op["responses"][next(iter(op["responses"]))]["headers"]


def test_every_operation_has_problem_error_response_except_health_and_packs() -> None:
    for path, _method, op in _operations():
        if path in ("/health", "/packs"):
            continue
        assert op["responses"]["default"] == {"$ref": "#/components/responses/Problem"}, path


def test_paths_are_domain_agnostic() -> None:
    names = " ".join([*SPEC["paths"], *SPEC["components"]["schemas"]]).lower()
    assert "dns" not in names


# ---------------------------------------------------------------- examples


HIGHLIGHT_REQUEST: dict[str, Any] = {
    "event_type": "content.highlighted",
    "event_version": 1,
    "occurred_at": "2026-09-22T10:10:00Z",
    "idempotency_key": "web:01J9ZQ4KEY0001",
    "attempt_id": "att_01J9ZQ4ATT0001",
    "payload": _event("content.highlighted")["payload"],
}


def test_client_event_request_valid() -> None:
    assert _is_valid(HIGHLIGHT_REQUEST, "ClientEventRequest")
    step = {
        **HIGHLIGHT_REQUEST,
        "event_type": "visualization.step_selected",
        "attempt_id": None,
        "payload": _event("visualization.step_selected")["payload"],
    }
    assert _is_valid(step, "ClientEventRequest")


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"event_type": "session.started"}, id="not-a-client-event-type"),
        pytest.param({"event_version": 2}, id="unknown-version"),
        pytest.param({"idempotency_key": None}, id="null-idempotency-key"),
        pytest.param({"actor": "learner"}, id="server-supplied-field"),
        pytest.param({"payload": {"highlight_id": "hl_1"}}, id="payload-fails-payload-schema"),
    ],
)
def test_client_event_request_rejected(mutation: dict[str, Any]) -> None:
    request = copy.deepcopy(HIGHLIGHT_REQUEST)
    request.update(mutation)
    assert not _is_valid(request, "ClientEventRequest")


def test_chat_message_request_and_exchange() -> None:
    requested = _event("assistant.message_requested")
    request = {
        "idempotency_key": "web:01J9ZQ4KEY0002",
        "occurred_at": "2026-09-22T10:15:00Z",
        "thread_id": None,
        "attempt_id": None,
        "text": requested["payload"]["text"],
        "references": [{"type": "highlight", "id": "hl_01J9ZQ4HL0001"}],
    }
    assert _is_valid(request, "ChatMessageRequest")
    assert not _is_valid({**request, "references": [{"type": "file"}]}, "ChatMessageRequest")

    generated = _event("assistant.message_generated")
    assert _is_valid({"request": requested, "reply": generated}, "ChatExchange")
    assert not _is_valid({"request": generated, "reply": requested}, "ChatExchange")


def test_timeline_page_uses_stored_events() -> None:
    events = [_event(t) for t in ("session.started", "terminal.command", "terminal.output")]
    page = {"events": events, "last_position": 3, "has_more": False}
    assert _is_valid(page, "TimelinePage")
    unstored = {k: v for k, v in events[0].items() if k != "position"}
    assert not _is_valid({**page, "events": [unstored]}, "TimelinePage")


ACTIVITY = {
    "activity_definition_id": "diagnose-resolver-failure-v1",
    "activity_definition_hash": "sha256:" + "a" * 64,
    "title": "Restore name resolution",
    "activity_type": "diagnose",
    "skill_ids": ["network.dns.resolution"],
    "lab_backed": True,
    "instructions": {"mission": "Service cannot resolve its dependency. Fix it."},
}

LAB = {
    "lab_instance_id": "lab_01J9ZQ4LAB0001",
    "attempt_id": "att_01J9ZQ4ATT0001",
    "status": "ready",
    "terminal_id": "term_01J9ZQ4TERM0001",
    "terminal_path": "/labs/lab_01J9ZQ4LAB0001/terminal",
    "replaced_by_lab_instance_id": None,
}


def test_activity_view_withholds_secret_fields() -> None:
    assert _is_valid(ACTIVITY, "ActivityView")
    for secret in ("reference_solution", "environment", "checks", "evaluator", "hints"):
        assert not _is_valid({**ACTIVITY, secret: "x"}, "ActivityView"), secret
    for kind in ("reference_solution", "environment", "activity_template", "evaluator"):
        assert not _is_valid(kind, "ContentKind"), kind


def test_lab_state_reuses_ws_lab_status() -> None:
    assert _is_valid(LAB, "LabState")
    assert not _is_valid({**LAB, "status": "running"}, "LabState")


def test_attempt_state_completed_with_result() -> None:
    attempt = {
        "attempt_id": "att_01J9ZQ4ATT0001",
        "session_id": "ses_01J9ZQ4SES0001",
        "activity": ACTIVITY,
        "status": "completed",
        "started_at": "2026-09-22T10:01:00Z",
        "lab": LAB,
        "last_submission_error": None,
        "result": {
            "outcome": "passed",
            "evaluation": _event("evaluation.completed"),
            "evidence": [_event("evidence.created")],
            "skill_updates": [_event("learner_skill.updated")],
            "completion": _event("activity.completed"),
        },
    }
    assert _is_valid(attempt, "AttemptState")
    swapped = copy.deepcopy(attempt)
    swapped["result"]["evidence"] = [_event("evaluation.completed")]
    assert not _is_valid(swapped, "AttemptState")


def test_session_state_for_resume() -> None:
    state = {
        "session": {
            "session_id": "ses_01J9ZQ4SES0001",
            "learner_id": "usr_01J9ZQ4USR0001",
            "pack": _event("session.started")["payload"]["provenance"]["pack"],
            "started_at": "2026-09-22T10:00:00Z",
            "session_started_event_id": "evt_01J9ZQ4EVT0001",
        },
        "last_position": 16,
        "active_attempt": None,
        "ui_state": {
            "open_content": _event("content.opened")["payload"],
            "visualization_steps": [_event("visualization.step_selected")["payload"]],
        },
    }
    assert _is_valid(state, "SessionState")


def test_problem_shape() -> None:
    problem = {
        "type": "https://morphloop.dev/problems/validation-failed",
        "title": "Request body failed validation",
        "status": 422,
        "code": "validation-failed",
        "errors": [{"path": "$.payload.end_offset", "message": "-1 is less than 0"}],
    }
    assert _is_valid(problem, "Problem")
    assert not _is_valid({**problem, "code": "teapot"}, "Problem")
