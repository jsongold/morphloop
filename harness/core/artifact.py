"""Artifact: the base type of things a learner can operate or view (#34, #48).

An artifact has an ``id``, a ``type`` and ``labels``. ``type`` is the name a
subclass registers, not an enum: defining a subclass (in the domain adapter
layer or a later resource) registers it, and core keeps no list of values.
A subclass also declares the client ``capabilities`` it needs (e.g.
``terminal``), so a client that lacks one can refuse to render it.

Subclasses are frozen dataclasses like the Port value types
(``harness.core.ports``); label validation is :mod:`harness.core.labels`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

_REGISTRY: dict[str, type[Artifact]] = {}


class UnknownArtifactTypeError(LookupError):
    """No artifact subclass is registered under the requested type name."""


# No slots on the base: zero-arg super() in __init_subclass__ breaks on a slots dataclass.
@dataclass(frozen=True, kw_only=True)
class Artifact:
    """Base class; subclasses set ``type`` (and optionally ``capabilities``)."""

    type: ClassVar[str]
    capabilities: ClassVar[frozenset[str]] = frozenset()

    id: str
    labels: frozenset[str] = field(default_factory=frozenset)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        type_name = cls.__dict__.get("type")
        if not isinstance(type_name, str) or not type_name:
            raise TypeError(f"Artifact {cls.__qualname__} must declare its own type name")
        existing = _REGISTRY.get(type_name)
        # @dataclass(slots=True) re-creates the class (its __qualname__ is not yet set), so the
        # same module + name counts as the same class.
        if existing is not None and (existing.__module__, existing.__name__) != (
            cls.__module__,
            cls.__name__,
        ):
            raise TypeError(f"artifact type {type_name!r} is already registered by {existing!r}")
        _REGISTRY[type_name] = cls

    def __post_init__(self) -> None:
        if type(self) is Artifact:
            raise TypeError("Artifact is abstract; instantiate a registered subclass")
        if not self.id:
            raise ValueError("artifact id must not be empty")


def registered_artifact_types() -> Mapping[str, type[Artifact]]:
    """Every registered artifact subclass by type name (read-only)."""
    return MappingProxyType(_REGISTRY)


def artifact_class(type_name: str) -> type[Artifact]:
    """The subclass registered as ``type_name``; raises :class:`UnknownArtifactTypeError`."""
    try:
        return _REGISTRY[type_name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise UnknownArtifactTypeError(
            f"unknown artifact type {type_name!r} (registered: {known})"
        ) from None
