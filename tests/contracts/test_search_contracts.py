"""Contract tests for contracts/schemas/search/ (issue #180 S1).

Checks the request/response schemas themselves, the $id convention, and
validates example instances -- including that `SearchHit.to_dict()` (the
harness/core/ports/search.py value type) conforms to the response schema's
result item shape.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.core.ports.search import KeywordSearchRequest, SearchHit, SemanticSearchRequest
from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, load_schema, validate

REQUEST = "schemas/search/request.json"
RESPONSE = "schemas/search/response.json"
ID_BASE = "https://morphloop.dev/contracts/"


@pytest.mark.parametrize("relative_path", [REQUEST, RESPONSE])
def test_schema_is_well_formed(relative_path: str) -> None:
    schema = load_schema(relative_path)
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("relative_path", [REQUEST, RESPONSE])
def test_id_matches_repo_convention(relative_path: str) -> None:
    schema = load_schema(relative_path)
    assert schema["$id"] == ID_BASE + relative_path


@pytest.mark.parametrize(
    "instance",
    [
        {"query": "how does the resolver retry"},
        {"query": "how does the resolver retry", "mode": "hybrid"},
        {"query": "ttl", "mode": "keyword", "resources": ["textbook_block"], "limit": 10},
    ],
)
def test_valid_requests(instance: dict[str, Any]) -> None:
    validate(instance, REQUEST)


@pytest.mark.parametrize(
    "instance",
    [
        {},  # missing query
        {"query": ""},  # empty query
        {"query": "ttl", "mode": "fuzzy"},  # not a legal mode
        {"query": "ttl", "extra": "nope"},  # closed object
    ],
)
def test_invalid_requests(instance: dict[str, Any]) -> None:
    with pytest.raises(ContractViolation):
        validate(instance, REQUEST)


@pytest.mark.parametrize(
    "instance",
    [
        {"results": []},
        {
            "results": [
                {
                    "kind": "textbook_block",
                    "id": "blk_1",
                    "parent_id": "doc_1",
                    "score": 0.83,
                    "source": "hybrid",
                },
                {"kind": "drill_item", "id": "item_1", "score": 0.5, "source": "keyword"},
            ]
        },
    ],
)
def test_valid_responses(instance: dict[str, Any]) -> None:
    validate(instance, RESPONSE)


@pytest.mark.parametrize(
    "instance",
    [
        {"results": [{"kind": "textbook_block", "id": "blk_1", "score": 0.5}]},  # missing source
        {
            "results": [
                {"kind": "memo_entry", "id": "e", "parent_id": "", "score": 1, "source": "keyword"}
            ]
        },
        {"results": [{"kind": "textbook_block", "id": "blk_1", "score": 0.5, "source": "vibes"}]},
        # a drill item's expected-answer field must never be a legal result property
        {
            "results": [
                {
                    "kind": "drill_item",
                    "id": "item_1",
                    "score": 0.5,
                    "source": "keyword",
                    "expected": "42",
                }
            ]
        },
    ],
)
def test_invalid_responses(instance: dict[str, Any]) -> None:
    with pytest.raises(ContractViolation):
        validate(instance, RESPONSE)


def test_search_hit_to_dict_matches_response_item_shape() -> None:
    hit = SearchHit(kind="memo_entry", id="ent_1", score=1.0, source="semantic")
    validate({"results": [hit.to_dict()]}, RESPONSE)


def test_readme_states_drill_answers_are_never_searchable() -> None:
    readme = (CONTRACTS_DIR / "schemas" / "search" / "README.md").read_text(encoding="utf-8")
    assert "never searchable" in readme.lower()
    assert "sys:holdout" in readme


def test_search_hit_parent_id_round_trips_to_wire() -> None:
    hit = SearchHit(kind="memo_entry", id="ent_1", parent_id="ws_1", score=1.0, source="keyword")
    assert hit.to_dict()["parent_id"] == "ws_1"
    validate({"results": [hit.to_dict()]}, RESPONSE)


def test_search_requests_require_learner_scope() -> None:
    with pytest.raises(ValueError, match="user_id"):
        KeywordSearchRequest(text="ttl", user_id="", limit=5)
    with pytest.raises(ValueError, match="user_id"):
        SemanticSearchRequest(embedding=[0.1], model="m", user_id="", limit=5)
    with pytest.raises(ValueError, match="model"):
        SemanticSearchRequest(embedding=[0.1], model="", user_id="u1", limit=5)
    assert KeywordSearchRequest(text="ttl", user_id="u1", limit=5).user_id == "u1"
