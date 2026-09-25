"""Block plaintext matches every shared vector in contracts/fixtures/plaintext/ (#55)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.core.textbook.plaintext import block_plaintext
from harness.testing.contracts import CONTRACTS_DIR

VECTORS = sorted((CONTRACTS_DIR / "fixtures" / "plaintext").glob("*.json"))


def test_vectors_exist() -> None:
    assert len(VECTORS) >= 10


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_vector(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    assert set(case) == {"markdown", "plaintext"}
    assert block_plaintext(case["markdown"]) == case["plaintext"]
