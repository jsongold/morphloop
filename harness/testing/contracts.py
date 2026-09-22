"""Helpers for validating payloads against the `contracts/` JSON Schemas.

`contracts/` is the language-neutral source of truth for wire formats
(events, WebSocket messages, pack content, LLM outputs). No types are
generated from it; instead, tests load real instances and validate them
against these schemas with `validate()` below.

Schemas are Draft 2020-12 and may reference each other with a local `$ref`
resolved by `$id`, as long as they live under the same contracts directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource


class ContractViolation(AssertionError):
    """Raised when an instance fails to validate against a contract schema."""


def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for the repo root (marked by pyproject.toml)."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ContractViolation(
        f"could not locate repo root (no pyproject.toml found) walking up from {start}"
    )


CONTRACTS_DIR: Path = _find_repo_root(Path(__file__).resolve().parent) / "contracts"


def _resolve_contracts_dir(contracts_dir: Path | None) -> Path:
    return contracts_dir if contracts_dir is not None else CONTRACTS_DIR


def load_schema(relative_path: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    """Load a single JSON Schema document by path relative to the contracts dir."""
    base = _resolve_contracts_dir(contracts_dir)
    schema_path = base / relative_path
    try:
        with schema_path.open(encoding="utf-8") as f:
            schema: dict[str, Any] = json.load(f)
    except FileNotFoundError as exc:
        raise ContractViolation(f"no schema at {schema_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractViolation(f"schema at {schema_path} is not valid JSON: {exc}") from exc
    return schema


def _build_registry(contracts_dir: Path) -> Registry[Any]:
    """Build a `referencing` registry of every `$id`-bearing schema under `contracts_dir`.

    This lets a schema `$ref` another schema in the same contracts directory by its
    `$id`, regardless of which files are involved.
    """
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted(contracts_dir.rglob("*.json")):
        with path.open(encoding="utf-8") as f:
            try:
                contents = json.load(f)
            except json.JSONDecodeError as exc:
                raise ContractViolation(f"schema at {path} is not valid JSON: {exc}") from exc
        if not isinstance(contents, dict) or "$id" not in contents:
            continue
        resource: Resource[Any] = Resource.from_contents(contents)
        resource_id = resource.id()
        if resource_id is None:
            continue
        resources.append((resource_id, resource))
    return Registry().with_resources(resources)


def _json_path(path: Any) -> str:
    parts = ["$"]
    for segment in path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}")
    return "".join(parts)


def _format_error(error: ValidationError) -> str:
    return f"{_json_path(error.absolute_path)}: {error.message}"


def validate(instance: object, relative_path: str, contracts_dir: Path | None = None) -> None:
    """Validate `instance` against the schema at `relative_path`.

    Raises `ContractViolation` (an `AssertionError`) listing every validation error
    found, each prefixed with the JSON path into `instance` where it occurred.
    """
    base = _resolve_contracts_dir(contracts_dir)
    schema = load_schema(relative_path, contracts_dir=base)

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractViolation(f"schema at {relative_path} is invalid: {exc.message}") from exc

    registry = _build_registry(base)
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        details = "\n".join(_format_error(error) for error in errors)
        raise ContractViolation(
            f"{len(errors)} validation error(s) against {relative_path}:\n{details}"
        )
