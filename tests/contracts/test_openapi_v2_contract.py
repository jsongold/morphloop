"""Contract tests for contracts/openapi/v0.2/ (ADR-0018, issue #44).

`root.yaml` keeps `paths: {}` on disk forever: each resource owns a URL ->
Path Item map in its own `paths/<resource>.yaml`, and
`harness.testing.openapi_v2.load_merged_openapi_v2_spec` merges every
`paths/*.yaml` into the document's `paths` at load time, rejecting a URL
declared twice. This means two resource PRs adding different URLs touch
different files and never need to edit (or conflict on) `root.yaml`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema_path import SchemaPath
from jsonschema_path.handlers import default_handlers
from openapi_spec_validator.validation import OpenAPIV31SpecValidator

from harness.testing.openapi_v2 import V2_DIR, DuplicatePathError, load_merged_openapi_v2_spec

RESOURCES = [
    "session",
    "ws",
    "memo",
    "textbook",
    "drill",
    "artifact",
    "chat",
    "highlight",
    "events",
    "notebook",
]


def _no_contracts_schema_handler(uri: str) -> Any:
    # v0.2 skeleton does not yet reference any contracts/schemas/*.json by
    # $id (the new event envelope is issue #43); a resource PR that adds one
    # should extend this to resolve them.
    raise LookupError(f"contracts/openapi/v0.2 does not expect an external ref to {uri}")


def _schema_path(spec: dict[str, Any], base_uri: str) -> SchemaPath:
    handlers = dict(default_handlers)
    handlers["https"] = _no_contracts_schema_handler
    handlers["http"] = _no_contracts_schema_handler
    return SchemaPath.from_dict(spec, base_uri=base_uri, handlers=handlers)


MERGED_SPEC = load_merged_openapi_v2_spec()
ROOT = _schema_path(MERGED_SPEC, (V2_DIR / "root.yaml").resolve().as_uri())


def test_root_yaml_on_disk_keeps_paths_empty() -> None:
    on_disk = yaml.safe_load((V2_DIR / "root.yaml").read_text(encoding="utf-8"))
    assert on_disk["paths"] == {}


def test_is_openapi_3_1() -> None:
    assert ROOT["openapi"].startswith("3.1.")


def test_merged_document_is_valid_openapi_3_1() -> None:
    """Validates the merged spec with every $ref into components/ followed and
    checked, proving the split-file layout resolves to one document."""
    OpenAPIV31SpecValidator(ROOT).validate()


def _path_file(resource: str) -> dict[str, Any]:
    return yaml.safe_load((V2_DIR / "paths" / f"{resource}.yaml").read_text(encoding="utf-8")) or {}


def test_every_resource_has_its_own_path_file() -> None:
    # The `_stub` path is optional: a resource PR replaces it with real URLs.
    for resource in RESOURCES:
        assert (V2_DIR / "paths" / f"{resource}.yaml").is_file(), f"missing paths/{resource}.yaml"


def test_notebook_search_and_build_contracts_exist() -> None:
    assert {"get"} <= set(ROOT["paths"]["/notebook/search"].str_keys())
    assert {"post"} <= set(ROOT["paths"]["/notebook/workspace/build"].str_keys())


def test_notebook_build_schema_is_closed_and_labels_unique() -> None:
    build = MERGED_SPEC["paths"]["/notebook/workspace/build"]["post"]
    request = build["requestBody"]["content"]["application/json"]["schema"]
    assert request["properties"]["labels"]["uniqueItems"] is True
    response = build["responses"]["201"]["content"]["application/json"]["schema"]
    assert response["additionalProperties"] is False
    assert set(response["properties"]) == {"workspace", "documents", "drills"}
    assert response["properties"]["workspace"]["additionalProperties"] is False
    # A drill's `expected` answer must not be a legal field of the response.
    assert "expected" not in response["properties"]["drills"]["items"]["properties"]


def test_merge_is_the_union_of_every_path_file() -> None:
    expected: set[str] = set()
    for path_file in sorted((V2_DIR / "paths").glob("*.yaml")):
        expected |= set(yaml.safe_load(path_file.read_text(encoding="utf-8")) or {})
    assert set(ROOT["paths"].str_keys()) == expected


def test_remaining_stubs_are_tagged_and_share_problem() -> None:
    paths = ROOT["paths"]
    problem_code = ROOT["components"]["schemas"]["Problem"]["properties"]["code"].read_value()
    for resource in RESOURCES:
        if f"/{resource}/_stub" not in _path_file(resource):
            continue
        op = paths[f"/{resource}/_stub"]["get"]
        assert op["tags"].read_value() == [resource]
        error_schema = op["responses"]["default"]["content"]["application/problem+json"]["schema"]
        assert error_schema["properties"]["code"].read_value() == problem_code


def test_duplicate_url_across_resource_files_is_rejected(tmp_path: Path) -> None:
    v2_dir = tmp_path / "v0.2"
    (v2_dir / "paths").mkdir(parents=True)
    (v2_dir / "root.yaml").write_text(
        textwrap.dedent(
            """\
            openapi: 3.1.0
            info: {title: t, version: '1'}
            paths: {}
            """
        ),
        encoding="utf-8",
    )
    dup_path_item = textwrap.dedent(
        """\
        /dup:
          get:
            operationId: dup
            responses:
              '200': {description: ok}
        """
    )
    (v2_dir / "paths" / "a.yaml").write_text(dup_path_item, encoding="utf-8")
    (v2_dir / "paths" / "b.yaml").write_text(dup_path_item, encoding="utf-8")
    with pytest.raises(DuplicatePathError, match="/dup"):
        load_merged_openapi_v2_spec(v2_dir)


def test_components_common_has_problem() -> None:
    common = yaml.safe_load((V2_DIR / "components" / "common.yaml").read_text(encoding="utf-8"))
    assert "Problem" in common
    assert common["Problem"]["required"] == ["type", "title", "status", "code"]


def test_component_files_are_common_or_per_resource() -> None:
    # Resource-specific components live in components/<resource>.yaml.
    allowed = {"common.yaml", *(f"{r}.yaml" for r in RESOURCES)}
    for path in (V2_DIR / "components").glob("*.yaml"):
        assert path.name in allowed, path.name


def test_paths_are_domain_agnostic() -> None:
    names = " ".join([*ROOT["paths"].str_keys(), *ROOT["components"]["schemas"].str_keys()]).lower()
    assert "dns" not in names


def test_artifact_spec_response_is_type_neutral() -> None:
    schema = MERGED_SPEC["paths"]["/artifact-specs/{spec_id}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema["required"] == ["id", "type", "labels", "spec"]
    assert schema["properties"]["spec"] == {"type": "object", "additionalProperties": True}


def test_readme_documents_the_merge_and_never_edit_root_rule() -> None:
    readme = (V2_DIR / "README.md").read_text(encoding="utf-8")
    assert "paths/<resource>.yaml" in readme
    assert "components/<resource>.yaml" in readme
    assert "openapi_v2" in readme


# v0.4 (issue #168): every list operation pages with ?cursor=&limit= and
# answers with an optional `next_cursor` (URL, list key).
LIST_OPERATIONS = [
    ("/ws", "items"),
    ("/sessions", "items"),
    ("/ws/{ws_id}/threads", "items"),
    ("/ws/{ws_id}/threads/{thread_id}/messages", "messages"),
    ("/ws/{ws_id}/highlights", "highlights"),
    ("/ws/{ws_id}/memo/entries", "entries"),
    ("/drills", "items"),
    ("/ws/{ws_id}/drills/answers", "items"),
    ("/notebook/search", "results"),
]


def _paged_object_schema(url: str) -> dict[str, Any]:
    schema = MERGED_SPEC["paths"][url]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    # /sessions keeps its pre-v0.4 bare-array shape as an `anyOf` branch.
    objects = [s for s in schema.get("anyOf", [schema]) if s.get("type") == "object"]
    assert len(objects) == 1, url
    return objects[0]


@pytest.mark.parametrize(("url", "key"), LIST_OPERATIONS)
def test_list_operation_declares_cursor_and_limit(url: str, key: str) -> None:
    params = ROOT["paths"][url]["get"]["parameters"]
    by_name = {}
    for i in range(len(params.read_value())):
        with params[i].open() as param:
            by_name[param["name"]] = param
    assert by_name["cursor"]["in"] == "query" and by_name["cursor"]["required"] is False
    assert by_name["limit"]["schema"]["type"] == "integer"
    assert by_name["limit"]["schema"]["minimum"] == 1


@pytest.mark.parametrize(("url", "key"), LIST_OPERATIONS)
def test_list_response_has_optional_next_cursor(url: str, key: str) -> None:
    schema = _paged_object_schema(url)
    assert schema["properties"]["next_cursor"]["type"] == ["string", "null"]
    assert "next_cursor" not in schema["required"]
    # Current (unpaged) and paged bodies both validate.
    Draft202012Validator(schema).validate({key: []})
    Draft202012Validator(schema).validate({key: [], "next_cursor": "c1"})
    Draft202012Validator(schema).validate({key: [], "next_cursor": None})


def test_sessions_list_still_accepts_the_bare_array() -> None:
    schema = MERGED_SPEC["paths"]["/sessions"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator(schema).validate([])


def test_problem_code_has_auth_and_limit_codes() -> None:
    with ROOT["components"]["schemas"]["Problem"].open() as problem:
        codes = problem["properties"]["code"]["enum"]
        description = problem["description"]
    for code, status in [
        ("unauthorized", 401),
        ("forbidden", 403),
        ("rate-limited", 429),
        ("auth-unavailable", 503),
    ]:
        assert code in codes
        assert f"`{code}` {status}" in description


def test_bearer_jwt_security_applies_to_every_operation() -> None:
    assert MERGED_SPEC["security"] == [{"bearer": []}]
    scheme = MERGED_SPEC["components"]["securitySchemes"]["bearer"]
    assert (scheme["type"], scheme["scheme"], scheme["bearerFormat"]) == ("http", "bearer", "JWT")
    for item in MERGED_SPEC["paths"].values():
        for op in item.values():
            if isinstance(op, dict) and "responses" in op:
                assert "security" not in op, op.get("operationId")


def test_socket_ticket_endpoint() -> None:
    op = MERGED_SPEC["paths"]["/auth/socket-tickets"]["post"]
    schema = op["responses"]["201"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["ticket", "expires_at"]
    assert schema["additionalProperties"] is False
    Draft202012Validator(schema).validate({"ticket": "t", "expires_at": "2026-01-01T00:00:00Z"})
    readme = (V2_DIR.parents[1] / "schemas" / "websocket" / "README.md").read_text(encoding="utf-8")
    assert "?ticket=" in readme and "/auth/socket-tickets" in readme
