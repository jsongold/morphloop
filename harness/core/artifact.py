"""Artifact: the base type of things a learner can operate or view (#34, #48, #95).

An artifact has an ``id``, a ``type`` and ``labels``. ``type`` is the name a
subclass registers, not an enum: the SDK holds no concrete artifact type
(ADR-0018 §19); an app subclasses :class:`Artifact` and hands its types to the
pack importer and :func:`harness.api.app.create_app`. A subclass also declares
the client ``capabilities`` it needs (e.g. ``terminal``), so a client that lacks
one can refuse to render it.

Per type, the pack's ``spec`` object is validated by:

- ``spec_schema``: a JSON Schema (draft 2020-12, may ``$ref`` the contracts),
  checked when the class is defined; the default accepts any object;
- ``validate_spec(spec, pack)``: cross-file rules the schema cannot express,
  run only when the schema passed; returns one message per problem.

Defining a subclass also records it in a process-wide registry
(:func:`registered_artifact_types`), the convenience default when no explicit
types are passed. Subclasses are frozen dataclasses like the Port value types
(``harness.core.ports``); label validation is :mod:`harness.core.labels`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_json, to_plain_object

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

_REGISTRY: dict[str, type[Artifact]] = {}


class UnknownArtifactTypeError(LookupError):
    """No artifact subclass is registered under the requested type name."""


class ArtifactSpecNotFoundError(LookupError):
    """No artifact spec in the loaded pack has the requested id."""


# No slots on the base: zero-arg super() in __init_subclass__ breaks on a slots dataclass.
@dataclass(frozen=True, kw_only=True)
class Artifact:
    """Base class; subclasses set ``type`` (and optionally ``capabilities``)."""

    type: ClassVar[str]
    capabilities: ClassVar[frozenset[str]] = frozenset()
    spec_schema: ClassVar[JsonObject] = MappingProxyType({"type": "object"})
    """JSON Schema (draft 2020-12) of this type's pack ``spec``; any object by default."""

    id: str
    labels: frozenset[str] = field(default_factory=frozenset)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        type_name = cls.__dict__.get("type")
        if not isinstance(type_name, str) or not type_name:
            raise TypeError(f"Artifact {cls.__qualname__} must declare its own type name")
        if "spec_schema" in cls.__dict__:
            try:
                Draft202012Validator.check_schema(to_plain_object(cls.spec_schema))
            except SchemaError as exc:
                raise TypeError(
                    f"artifact {type_name!r} spec_schema is invalid: {exc.message}"
                ) from exc
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

    @classmethod
    def validate_spec(cls, spec: JsonObject, pack: PackV2) -> Iterable[str]:
        """Problems of a schema-valid ``spec`` that need the whole pack; none by default."""
        return ()

    @classmethod
    def learner_view(cls, spec: JsonObject) -> JsonObject:
        """The part of a validated spec safe to show before starting the artifact."""
        return spec


def learner_artifact_spec(
    pack: PackV2, spec_id: str, artifact_types: Iterable[type[Artifact]]
) -> dict[str, PlainJson]:
    """Find a loaded spec and let its registered type select learner-visible fields."""
    for doc in pack.documents["artifacts"].values():
        if doc["id"] == spec_id:
            types = {cls.type: cls for cls in artifact_types}
            type_name = str(doc["type"])
            cls = types.get(type_name)
            if cls is None:
                raise UnknownArtifactTypeError(f"artifact type {type_name!r} is not registered")
            spec = doc["spec"]
            if not isinstance(spec, Mapping):
                raise TypeError("validated artifact spec must be an object")
            return {
                "id": str(doc["id"]),
                "type": type_name,
                "labels": to_plain_json(doc["labels"]),
                "spec": to_plain_object(cls.learner_view(spec)),
            }
    raise ArtifactSpecNotFoundError(f"artifact spec {spec_id!r} not found")


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
