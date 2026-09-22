"""Tests for harness.testing.contracts against fixture schemas.

These tests always pass `contracts_dir` explicitly (pointing at
`tests/contracts/fixtures/`) so they never touch the real `contracts/` tree.
"""

from pathlib import Path

import pytest

from harness.testing.contracts import ContractViolation, validate

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_valid_instance_passes() -> None:
    validate(
        {"name": "foo", "widget": {"id": 1}},
        "main.schema.json",
        contracts_dir=FIXTURES_DIR,
    )


def test_invalid_instance_raises_with_json_paths() -> None:
    with pytest.raises(ContractViolation) as excinfo:
        validate(
            {"name": 123, "widget": {"id": "not-an-int"}},
            "main.schema.json",
            contracts_dir=FIXTURES_DIR,
        )
    message = str(excinfo.value)
    assert "$.name" in message
    assert "$.widget.id" in message


def test_invalid_schema_is_rejected() -> None:
    with pytest.raises(ContractViolation, match="invalid.schema.json"):
        validate({}, "invalid.schema.json", contracts_dir=FIXTURES_DIR)


def test_cross_file_ref_resolves() -> None:
    # main.schema.json's "widget" property is a $ref into widget.schema.json,
    # resolved by $id rather than by file path.
    with pytest.raises(ContractViolation) as excinfo:
        validate(
            {"name": "foo", "widget": {"id": "not-an-int"}},
            "main.schema.json",
            contracts_dir=FIXTURES_DIR,
        )
    assert "$.widget.id" in str(excinfo.value)
