"""A TestClient over the real app, wired to the learning-loop test fakes.

Nothing here touches Docker, Postgres, an LLM or the network: the app is built
with an explicit :class:`~harness.api.backend.Backend` holding the same loop
``tests/core`` drives directly, so an API test exercises the real routing,
serialization and error mapping against the same in-memory pack.

Responses are checked against ``contracts/openapi/v0.1.yaml`` and WebSocket
messages against ``contracts/schemas/websocket/``; both are the language-neutral
source of truth and neither is generated from (ADR-0017).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from harness.api.app import create_app
from harness.api.backend import Backend
from harness.testing.contracts import CONTRACTS_DIR, ContractViolation, load_schema, validate

# The loop fixture is shared with tests/core rather than copied. pytest only
# puts a test directory on sys.path once it collects from it, so running
# `pytest tests/api` alone would not find it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from loop_harness import LoopFixture, build_loop  # noqa: E402

OPENAPI_URI = "https://morphloop.dev/contracts/openapi/v0.1.yaml"
WS_MESSAGE_SCHEMA = "schemas/websocket/envelope/message.json"

SPEC: dict[str, Any] = yaml.safe_load(
    (CONTRACTS_DIR / "openapi" / "v0.1.yaml").read_text(encoding="utf-8")
)


def _registry() -> Registry[Any]:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted(CONTRACTS_DIR.rglob("*.json")):
        contents = load_schema(str(path.relative_to(CONTRACTS_DIR)))
        if "$id" in contents:
            resources.append((contents["$id"], Resource.from_contents(contents)))
    resources.append((OPENAPI_URI, DRAFT202012.create_resource(SPEC)))
    return Registry().with_resources(resources)


_REGISTRY = _registry()


def assert_component(instance: object, component: str, *, optional: Sequence[str] = ()) -> None:
    """Validate a response body against an OpenAPI component schema.

    ``optional`` drops fields from the component's ``required`` list, for the
    one place where the contract asks for data the harness does not record; the
    test that uses it says which gap it is covering.
    """
    schema: dict[str, Any] = {"$ref": f"{OPENAPI_URI}#/components/schemas/{component}"}
    if optional:
        schema = deepcopy(SPEC["components"]["schemas"][component])
        schema["required"] = [f for f in schema.get("required", ()) if f not in optional]
    validator = Draft202012Validator(schema, registry=_REGISTRY)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        details = "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise ContractViolation(f"{len(errors)} error(s) against {component}:\n{details}")


def assert_ws_message(message: object) -> None:
    """Validate a WebSocket frame against ``ws/envelope/message.json``."""
    validate(message, WS_MESSAGE_SCHEMA)


def build_app(fixture: LoopFixture | None = None) -> tuple[Any, LoopFixture]:
    """The real FastAPI app over a loop wired to fakes.

    Passing the backend explicitly also keeps startup from wiring the real
    adapters, so no test reaches Postgres or the Docker daemon.
    """
    loop_fixture = fixture if fixture is not None else build_loop()
    app = create_app(Backend(loop=loop_fixture.loop, store=loop_fixture.store))
    return app, loop_fixture
