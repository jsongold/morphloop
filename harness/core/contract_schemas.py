"""Runtime access to the ``contracts/`` JSON Schemas for core.

Core validates pack files (Importer) and LLM outputs (learner model, ADR-0013)
against ``contracts/schemas/``. This module loads every ``$id``-bearing schema
of a contracts directory into one ``referencing`` registry, validates instances
by ``$id`` and bundles a schema (external ``$ref``s inlined) for
:class:`~harness.core.ports.LLMRequest`. It is the core-side counterpart of
``harness.testing.contracts``, which core must not import.

Locating ``contracts/`` at runtime (:func:`locate_contracts_dir`), first match wins:

1. the ``contracts_dir`` argument (wiring or tests pass it explicitly);
2. the ``MORPHLOOP_CONTRACTS_DIR`` environment variable;
3. walking up from this file to the first ancestor holding
   ``contracts/schemas/`` (the repository checkout; an editable install).

A packaged deployment that does not ship the repository must use 1 or 2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from harness.core.ports import PlainJson, to_plain_json
from harness.core.settings import Settings

CONTRACTS_DIR_ENV = "MORPHLOOP_CONTRACTS_DIR"
CONTRACTS_ID_BASE = "https://morphloop.dev/contracts/"
_MAX_BUNDLE_DEPTH = 64


class ContractsNotFoundError(RuntimeError):
    """No usable ``contracts/`` directory could be located."""


class UnknownSchemaError(LookupError):
    """No schema with the requested ``$id`` is loaded."""


class ContractValidationError(ValueError):
    """An instance does not validate against a contract schema."""

    def __init__(self, schema_id: str, errors: list[str]) -> None:
        super().__init__(
            f"{len(errors)} validation error(s) against {schema_id}:\n" + "\n".join(errors)
        )
        self.schema_id = schema_id
        self.errors = errors


def locate_contracts_dir(contracts_dir: Path | str | None = None) -> Path:
    """Return the contracts directory (see the module docstring for the order)."""
    env_contracts_dir = Settings().morphloop_contracts_dir
    if contracts_dir is not None:
        candidate = Path(contracts_dir)
        source = "argument"
    elif env_contracts_dir:
        candidate = Path(env_contracts_dir)
        source = CONTRACTS_DIR_ENV
    else:
        for parent in Path(__file__).resolve().parents:
            if (parent / "contracts" / "schemas").is_dir():
                return parent / "contracts"
        raise ContractsNotFoundError(
            f"no contracts/schemas/ above {Path(__file__).resolve()}; set {CONTRACTS_DIR_ENV}"
        )
    if not (candidate / "schemas").is_dir():
        raise ContractsNotFoundError(f"{candidate} (from {source}) has no schemas/ directory")
    return candidate


def _json_path(path: Any) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


class ContractSchemas:
    """Every ``$id``-bearing schema under one contracts directory."""

    def __init__(self, contracts_dir: Path) -> None:
        self.contracts_dir = contracts_dir
        resources: list[tuple[str, Resource[Any]]] = []
        for path in sorted(contracts_dir.rglob("*.json")):
            contents = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(contents, dict) or "$id" not in contents:
                continue
            resource: Resource[Any] = Resource.from_contents(contents)
            resource_id = resource.id()
            if resource_id is not None:
                resources.append((resource_id, resource))
        self._ids = frozenset(resource_id for resource_id, _ in resources)
        self._registry: Registry[Any] = Registry().with_resources(resources).crawl()
        self._validators: dict[str, Draft202012Validator] = {}

    @classmethod
    def load(cls, contracts_dir: Path | str | None = None) -> ContractSchemas:
        """Load from :func:`locate_contracts_dir`."""
        return cls(locate_contracts_dir(contracts_dir))

    @staticmethod
    def id_for(relative_path: str) -> str:
        """``$id`` of the schema at ``relative_path`` under ``contracts/``."""
        return CONTRACTS_ID_BASE + relative_path

    def has(self, schema_id: str) -> bool:
        return schema_id in self._ids

    def _validator(self, schema_id: str) -> Draft202012Validator:
        if schema_id not in self._ids:
            raise UnknownSchemaError(schema_id)
        validator = self._validators.get(schema_id)
        if validator is None:
            validator = Draft202012Validator({"$ref": schema_id}, registry=self._registry)
            self._validators[schema_id] = validator
        return validator

    def errors(self, instance: object, schema_id: str) -> list[str]:
        """Return ``"$.path: message"`` for every error (empty when valid).

        ``Mapping`` / ``Sequence`` JSON values are copied to plain ``dict`` /
        ``list`` first, since jsonschema only treats ``dict`` as an object.
        """
        if isinstance(instance, Mapping | list | tuple):
            instance = to_plain_json(instance)
        found: list[ValidationError] = sorted(
            self._validator(schema_id).iter_errors(instance), key=lambda e: list(e.path)
        )
        return [f"{_json_path(e.absolute_path)}: {e.message}" for e in found]

    def validate(self, instance: object, schema_id: str) -> None:
        """Raise :class:`ContractValidationError` unless ``instance`` is valid."""
        errors = self.errors(instance, schema_id)
        if errors:
            raise ContractValidationError(schema_id, errors)

    def bundle(self, schema_id: str) -> dict[str, PlainJson]:
        """Return the schema with every ``$ref`` inlined and ``$defs`` dropped.

        This is the ``output_schema`` handed to an LLM provider
        (``contracts/schemas/llm/README.md``); validation still uses the
        original schema through :meth:`validate`.
        """
        if schema_id not in self._ids:
            raise UnknownSchemaError(schema_id)
        resolved = self._registry.resolver().lookup(schema_id)
        bundled = self._inline(resolved.contents, resolved.resolver, (), top=True)
        assert isinstance(bundled, dict)
        return bundled

    def _inline(self, node: Any, resolver: Any, stack: tuple[str, ...], *, top: bool) -> PlainJson:
        if len(stack) > _MAX_BUNDLE_DEPTH:
            raise ValueError("schema $ref chain too deep to bundle")
        if isinstance(node, list):
            return [self._inline(item, resolver, stack, top=False) for item in node]
        if not isinstance(node, dict):
            assert node is None or isinstance(node, str | int | float | bool)
            return node
        if "$id" in node and not top:
            resolver = resolver.in_subresource(Resource.from_contents(node))
        out: dict[str, PlainJson] = {}
        ref = node.get("$ref")
        if isinstance(ref, str):
            try:
                target = resolver.lookup(ref)
            except Unresolvable as exc:
                raise UnknownSchemaError(ref) from exc
            key = str(id(target.contents))
            if key in stack:
                raise ValueError(f"recursive $ref {ref!r} cannot be bundled")
            inlined = self._inline(target.contents, target.resolver, (*stack, key), top=False)
            if isinstance(inlined, dict):
                out.update(inlined)
        for name, value in node.items():
            if name == "$ref" or name == "$defs" or (not top and name in ("$id", "$schema")):
                continue
            out[name] = self._inline(value, resolver, stack, top=False)
        return out
