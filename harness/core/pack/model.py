"""Typed pack objects and the pack projection layout (ADR-0015).

The Importer writes, and :class:`~harness.core.pack.catalog.PackCatalog` reads,
three document projections through the event store Port. Every key starts with
the pack key ``<pack_id>/<pack_version>/<content_hash>``; ``/`` cannot occur in
a pack id, version label or content hash, so keys never collide.

``pack``
    key ``<pack key>``; the manifest plus the file index with hashes.
``pack_definition``
    key ``<pack key>/<kind>/<definition key>``; one parsed pack document.
    The definition key is the document ``id``; for ``prompt`` it is
    ``<prompt_id>@<prompt_version>``; for kinds without an id (``layout``,
    ``generation_record``) it is the pack-relative path.
``pack_secret``
    key ``<pack key>/reference_solution/<activity_id>``; reference solutions
    only (AC-J6, ADR-0014). They live in their own projection so that code
    building learner or tutor-hint context, which reads ``pack_definition``,
    cannot reach them by listing; only
    :meth:`~harness.core.pack.catalog.PackCatalog.get_reference_solution`
    (scoring, generation validation) reads it.

Exposure (``contracts/schemas/pack/README.md``, whitelist): ``skill``,
``visualization``, ``reference`` and ``layout`` are learner-facing; of an
``activity`` only :data:`LEARNER_VISIBLE_ACTIVITY_FIELDS` may reach the learner
or the tutor during an unfinished attempt (:func:`learner_view_of_activity`);
every other kind is withheld from both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from harness.core.ports import JsonObject, PlainJson, to_plain_object
from harness.core.registry.algorithms import RegistrySelection

PACK_PROJECTION = "pack"
DEFINITION_PROJECTION = "pack_definition"
SECRET_PROJECTION = "pack_secret"
PROJECTION_FORMAT = 1

type FileKind = Literal[
    "skill",
    "activity",
    "reference_solution",
    "environment",
    "activity_template",
    "generation_record",
    "evaluator",
    "visualization",
    "reference",
    "layout",
    "prompt",
]

KIND_SCHEMAS: Mapping[str, str | None] = {
    "skill": "schemas/pack/skill.json",
    "activity": "schemas/pack/activity-definition.json",
    "reference_solution": "schemas/pack/reference-solution.json",
    "environment": "schemas/pack/environment.json",
    "activity_template": "schemas/pack/activity-template.json",
    "generation_record": "schemas/pack/generation-record.json",
    "evaluator": "schemas/pack/evaluator.json",
    "visualization": "schemas/pack/visualization.json",
    "reference": "schemas/pack/reference.json",
    "layout": "schemas/pack/layout.json",
    "prompt": None,
}
MANIFEST_SCHEMA = "schemas/pack/manifest.json"
MANIFEST_NAMES = ("manifest.json", "manifest.yaml", "manifest.yml")

LEARNER_FACING_KINDS = frozenset({"skill", "visualization", "reference", "layout"})
LEARNER_VISIBLE_ACTIVITY_FIELDS = (
    "title",
    "activity_type",
    "skills",
    "difficulty",
    "instructions",
    "hints",
    "remediation",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PackRef:
    """Identity of one imported pack: the content hash is the identity (ADR-0010)."""

    pack_id: str
    pack_version: str
    content_hash: str

    @property
    def key(self) -> str:
        return f"{self.pack_id}/{self.pack_version}/{self.content_hash}"

    def definition_key(self, kind: str, definition_key: str) -> str:
        return f"{self.key}/{kind}/{definition_key}"

    def to_dict(self) -> dict[str, PlainJson]:
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class PackFileEntry:
    """One file of the pack: ``file_hash`` is SHA-256 of the raw bytes;
    ``document_hash`` is the JCS document hash (``None`` for prompts)."""

    path: str
    kind: str
    definition_key: str | None
    file_hash: str
    document_hash: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadedPack:
    """A pack as the runtime sees it, read from the ``pack`` projection."""

    ref: PackRef
    title: str
    pack_format: int
    domain_adapters: Mapping[str, str]
    registry: Mapping[str, RegistrySelection]
    manifest: JsonObject
    files: Sequence[PackFileEntry]


@dataclass(frozen=True, slots=True, kw_only=True)
class Definition:
    """One parsed pack document from ``pack_definition``.

    ``document`` is the full document. For an ``activity`` it must pass
    through :func:`learner_view_of_activity` before reaching a learner or a
    tutor in hint mode.
    """

    pack: PackRef
    kind: str
    key: str
    path: str
    document_hash: str
    document: JsonObject

    @property
    def learner_facing(self) -> bool:
        return self.kind in LEARNER_FACING_KINDS


@dataclass(frozen=True, slots=True, kw_only=True)
class Prompt:
    """A prompt file, identified by ``prompt_id`` + ``prompt_version``."""

    pack: PackRef
    prompt_id: str
    prompt_version: str
    path: str
    content_hash: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceSolution:
    """The secret fix of one activity (``pack_secret``). Never put it in
    learner context or tutor-hint context (AC-J6, ADR-0014)."""

    pack: PackRef
    solution_id: str
    activity_id: str
    path: str
    document_hash: str
    document: JsonObject


def learner_view_of_activity(document: JsonObject) -> dict[str, PlainJson]:
    """Only the whitelisted activity fields (``contracts/schemas/pack/README.md``)."""
    plain = to_plain_object(document)
    return {name: plain[name] for name in LEARNER_VISIBLE_ACTIVITY_FIELDS if name in plain}
