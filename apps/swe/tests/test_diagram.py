"""DiagramArtifact (#34, #56): schema plus cross-reference rules of a ``diagram`` spec."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from harness.sdk import ContractSchemas, artifact_class
from swe.artifacts.diagram import DiagramArtifact

type Doc = dict[str, Any]

DOC: Doc = {
    "id": "dns-resolution-flow",
    "type": "diagram",
    "labels": ["topic:network.dns.resolution"],
    "spec": {
        "title": "How api.internal becomes an address",
        "environment_bindings": {"service_name": "api.internal", "service_port": 9093},
        "diagram": {
            "type": "sequence",
            "actors": [
                {"id": "client", "label": "Client"},
                {"id": "resolver", "label": "Stub resolver"},
            ],
            "steps": [
                {
                    "id": "query",
                    "from": "client",
                    "to": "resolver",
                    "label": "query api.internal",
                    "explanation": "The client asks libc.",
                    "reality": {
                        "mechanism": "getaddrinfo()",
                        "observe": [
                            {
                                "argv": ["getent", "hosts", "api.internal"],
                                "purpose": "Run the same lookup.",
                                "look_for": "An address line.",
                            }
                        ],
                        "artifacts": [
                            {"kind": "file", "locator": "/etc/hosts", "description": "overrides"}
                        ],
                    },
                },
                {"id": "answer", "from": "resolver", "to": "client", "label": "A 10.0.0.1"},
            ],
        },
    },
}


def _steps(d: Doc) -> list[Doc]:
    steps: list[Doc] = d["spec"]["diagram"]["steps"]
    return steps


def _reality(d: Doc) -> Doc:
    reality: Doc = _steps(d)[0]["reality"]
    return reality


def _observe(d: Doc) -> Doc:
    observe: Doc = _reality(d)["observe"][0]
    return observe


def _no_reality(d: Doc) -> None:
    for step in _steps(d):
        step.pop("reality", None)


def _errors(doc: Doc) -> list[str]:
    return ContractSchemas.load().errors_against(doc["spec"], DiagramArtifact.spec_schema)


def test_registers_as_diagram_with_no_required_capabilities() -> None:
    assert artifact_class("diagram") is DiagramArtifact
    assert DiagramArtifact.capabilities == frozenset()


def test_valid_spec_passes_schema_and_validator() -> None:
    assert _errors(DOC) == []
    assert list(DiagramArtifact.validate_spec(DOC["spec"], pack=None)) == []  # type: ignore[arg-type]


# (edit, JSON path of the schema error inside the spec)
SCHEMA_CASES: dict[str, tuple[Callable[[Doc], object], str]] = {
    "missing title": (lambda d: d["spec"].pop("title"), "$"),
    "title too long": (lambda d: d["spec"].__setitem__("title", "x" * 201), "$.title"),
    "one actor": (
        lambda d: d["spec"]["diagram"].__setitem__("actors", d["spec"]["diagram"]["actors"][:1]),
        "$.diagram.actors",
    ),
    "actor id not a token": (
        lambda d: d["spec"]["diagram"]["actors"][0].__setitem__("id", "client actor"),
        "$.diagram.actors[0].id",
    ),
    "no reality": (_no_reality, "$.diagram.steps"),
    "step id not a token": (
        lambda d: _steps(d)[0].__setitem__("id", "bad step"),
        "$.diagram.steps[0].id",
    ),
    "step from not a string": (
        lambda d: _steps(d)[0].__setitem__("from", {"a": 1}),
        "$.diagram.steps[0].from",
    ),
    "explanation not a string": (
        lambda d: _steps(d)[0].__setitem__("explanation", {"text": "x"}),
        "$.diagram.steps[0].explanation",
    ),
    "mechanism too long": (
        lambda d: _reality(d).__setitem__("mechanism", "x" * 20001),
        ".reality.mechanism",
    ),
    "unknown reality key": (lambda d: _reality(d).__setitem__("artifact", []), ".reality"),
    "artifacts string": (lambda d: _reality(d).__setitem__("artifacts", ""), ".reality.artifacts"),
    "locator too long": (
        lambda d: _reality(d).__setitem__(
            "artifacts", [{"kind": "file", "locator": "/" * 1025, "description": "x"}]
        ),
        ".reality.artifacts[0].locator",
    ),
    "argv string": (lambda d: _observe(d).__setitem__("argv", "dig"), ".observe[0].argv"),
    "argv0 whitespace": (
        lambda d: _observe(d).__setitem__("argv", ["curl -sv"]),
        ".observe[0].argv[0]",
    ),
    "missing purpose": (lambda d: _observe(d).pop("purpose"), ".observe[0]"),
    "look_for empty": (lambda d: _observe(d).__setitem__("look_for", ""), ".observe[0].look_for"),
    "empty environment_bindings": (
        lambda d: d["spec"].__setitem__("environment_bindings", {}),
        "$.environment_bindings",
    ),
    "boolean environment binding": (
        lambda d: d["spec"]["environment_bindings"].__setitem__("verbose", True),
        "$.environment_bindings.verbose",
    ),
}


@pytest.mark.parametrize("case", SCHEMA_CASES)
def test_schema_rejects(case: str) -> None:
    edit, where = SCHEMA_CASES[case]
    doc = copy.deepcopy(DOC)
    edit(doc)
    errors = _errors(doc)
    assert any(where in e.split(": ")[0] for e in errors), errors


def test_step_referencing_unknown_actor_is_rejected() -> None:
    doc = copy.deepcopy(DOC)
    _steps(doc)[0]["from"] = "ghost"
    assert list(DiagramArtifact.validate_spec(doc["spec"], pack=None)) == [  # type: ignore[arg-type]
        "$.spec.diagram.steps[0].from: 'ghost' is not a declared actor"
    ]


def test_duplicate_ids_are_rejected() -> None:
    doc = copy.deepcopy(DOC)
    _steps(doc)[1]["id"] = "query"
    doc["spec"]["diagram"]["actors"][1]["id"] = "client"
    problems = list(DiagramArtifact.validate_spec(doc["spec"], pack=None))  # type: ignore[arg-type]
    assert "$.spec.diagram.actors[1].id: duplicate actor id 'client'" in problems
    assert "$.spec.diagram.steps[1].id: duplicate step id 'query'" in problems


def test_from_pack_spec_wraps_the_pack_spec_unchanged() -> None:
    art = DiagramArtifact.from_pack_spec(DOC)
    assert art.id == "dns-resolution-flow" and art.labels == frozenset(DOC["labels"])
    assert art.spec == DOC["spec"]
    with pytest.raises(FrozenInstanceError):
        art.id = "other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="not a diagram"):
        DiagramArtifact.from_pack_spec({**DOC, "type": "lab"})
