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


def test_splits_source_once_per_body() -> None:
    """``block_plaintext`` must not re-split the body once per paragraph (#94 perf)."""
    calls = 0
    real_splitlines = str.splitlines

    class _CountingStr(str):
        def splitlines(self, *args: object, **kwargs: object) -> list[str]:
            nonlocal calls
            calls += 1
            return real_splitlines(self, *args, **kwargs)

    body = _CountingStr("\n\n".join(f"Paragraph {i}." for i in range(20)))
    assert block_plaintext(body) == "\n".join(f"Paragraph {i}." for i in range(20))
    assert calls <= 1
