"""Validate lab route responses against the app's OpenAPI fragment plus the SDK base."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

import swe
from harness.testing.openapi_v2 import load_merged_openapi_v2_spec

LAB_SPEC: dict[str, Any] = yaml.safe_load(
    (Path(swe.__file__).parent / "openapi" / "artifact-lab.yaml").read_text(encoding="utf-8")
)
_ARTIFACTS_PATH: dict[str, Any] = load_merged_openapi_v2_spec()["paths"]["/ws/{ws_id}/artifacts"]
_BASE: dict[str, Any] = _ARTIFACTS_PATH["post"]["responses"]["201"]["content"]["application/json"][
    "schema"
]
_LIST: dict[str, Any] = _ARTIFACTS_PATH["get"]["responses"]["200"]["content"]["application/json"][
    "schema"
]


def _check(instance: object, schema: dict[str, Any]) -> None:
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(instance)]
    assert not errors, errors


def assert_lab_document(document: object) -> None:
    """A `lab` artifact document: the SDK base plus this app's `lab` state."""
    _check(document, _BASE)
    assert isinstance(document, dict) and document["type"] == "lab"
    _check(document["lab"], LAB_SPEC["schemas"]["lab"])


def assert_check_response(body: object) -> None:
    _check(body, LAB_SPEC["schemas"]["check_response"])


def assert_list_response(body: object) -> None:
    """A `GET /ws/{ws_id}/artifacts` response: type-neutral items only."""
    _check(body, _LIST)
