"""Artifact base: registration by type name, capabilities, lookup errors."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar

import pytest

from harness.core import artifact as artifact_module
from harness.core.artifact import (
    Artifact,
    UnknownArtifactTypeError,
    artifact_class,
    registered_artifact_types,
)


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    saved = dict(artifact_module._REGISTRY)
    yield
    artifact_module._REGISTRY.clear()
    artifact_module._REGISTRY.update(saved)


def test_subclass_registers_under_its_type_and_declares_capabilities() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Console(Artifact):
        type: ClassVar[str] = "test_console"
        capabilities: ClassVar[frozenset[str]] = frozenset({"terminal"})
        image: str

    art = Console(id="art_1", labels=frozenset({"dns"}), image="x")
    assert art.type == "test_console" and art.capabilities == {"terminal"}
    assert artifact_class("test_console") is Console
    assert registered_artifact_types()["test_console"] is Console
    with pytest.raises(FrozenInstanceError):
        art.id = "other"  # type: ignore[misc]


def test_unknown_type_lists_registered_types() -> None:
    class Plain(Artifact):
        type: ClassVar[str] = "test_plain"

    assert Plain(id="a").capabilities == frozenset()
    with pytest.raises(UnknownArtifactTypeError, match="'nope'.*test_plain"):
        artifact_class("nope")


def test_subclass_must_declare_type_and_names_are_unique() -> None:
    with pytest.raises(TypeError, match="must declare its own type"):

        class _NoType(Artifact):
            pass

    class _First(Artifact):
        type: ClassVar[str] = "test_dup"

    with pytest.raises(TypeError, match="already registered"):

        class _Second(Artifact):
            type: ClassVar[str] = "test_dup"


def test_base_is_not_instantiable_and_id_is_required() -> None:
    with pytest.raises(TypeError):
        Artifact(id="a")

    class Plain(Artifact):
        type: ClassVar[str] = "test_plain2"

    with pytest.raises(ValueError, match="id"):
        Plain(id="")
