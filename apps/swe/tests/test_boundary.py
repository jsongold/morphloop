"""The app depends on the SDK through ``harness.sdk`` only (ADR-0018 §19).

``lint-imports`` enforces the same rule from the repository root; this scan keeps
working once the app lives in its own repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

import swe

PACKAGE = Path(swe.__file__).parent
ALLOWED = {"harness.sdk", "domains.dns"}  # domains.dns moves into this app with #69


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_swe_imports_only_the_sdk_api() -> None:
    offending = {
        f"{path.relative_to(PACKAGE)}: {name}"
        for path in PACKAGE.rglob("*.py")
        for name in _imports(path)
        if name.split(".")[0] in {"harness", "domains"} and name not in ALLOWED
    }
    assert not offending, sorted(offending)
