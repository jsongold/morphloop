"""Read API over the pack projections (ADR-0015: runtime reads only the projection).

:class:`PackCatalog` turns the documents written by
:class:`~harness.core.pack.importer.PackImporter` into typed objects
(:mod:`harness.core.pack.model`). Reference solutions come only from
:meth:`PackCatalog.get_reference_solution`; nothing else here returns them.
"""

from __future__ import annotations

from collections.abc import Mapping

from harness.core.pack.model import (
    DEFINITION_PROJECTION,
    PACK_PROJECTION,
    SECRET_PROJECTION,
    Definition,
    LoadedPack,
    PackFileEntry,
    PackRef,
    Prompt,
    ReferenceSolution,
    import_seq,
)
from harness.core.ports import EventStore, JsonObject, JsonValue
from harness.core.registry.algorithms import RegistrySelection


class PackNotFoundError(LookupError):
    """No projection for the requested pack or definition."""


def _str(doc: JsonObject, name: str) -> str:
    value = doc.get(name)
    if not isinstance(value, str):
        raise ValueError(f"projection field {name!r} is not a string")
    return value


def _obj(value: JsonValue | None) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError("projection field is not an object")
    return value


def _ref(doc: JsonObject) -> PackRef:
    return PackRef(
        pack_id=_str(doc, "pack_id"),
        pack_version=_str(doc, "pack_version"),
        content_hash=_str(doc, "content_hash"),
    )


class PackCatalog:
    """Typed, read-only access to imported packs."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def list_packs(self, pack_id: str | None = None) -> list[PackRef]:
        """Imported packs (all, or of ``pack_id``), grouped by pack_id in import
        order: the last of each pack_id is its latest import (``import_seq``)."""
        prefix = "" if pack_id is None else pack_id + "/"
        with self._store.transaction() as tx:
            docs = tx.list_projection(PACK_PROJECTION, key_prefix=prefix)
        docs = sorted(docs, key=lambda kd: (_str(kd[1], "pack_id"), import_seq(kd[1]), kd[0]))
        return [_ref(doc) for _, doc in docs]

    def get_pack(self, ref: PackRef) -> LoadedPack:
        with self._store.transaction() as tx:
            doc = tx.get_projection(PACK_PROJECTION, ref.key)
        if doc is None:
            raise PackNotFoundError(ref.key)
        manifest = _obj(doc.get("manifest"))
        registry = _obj(manifest.get("registry"))
        adapters = _obj(manifest.get("domain_adapters"))
        files = doc.get("files")
        assert isinstance(files, list)
        entries = []
        for item in files:
            entry = _obj(item)
            key, doc_hash = entry.get("definition_key"), entry.get("document_hash")
            entries.append(
                PackFileEntry(
                    path=_str(entry, "path"),
                    kind=_str(entry, "kind"),
                    definition_key=key if isinstance(key, str) else None,
                    file_hash=_str(entry, "file_hash"),
                    document_hash=doc_hash if isinstance(doc_hash, str) else None,
                )
            )
        pack_format = manifest.get("pack_format")
        assert isinstance(pack_format, int)
        return LoadedPack(
            ref=_ref(doc),
            title=_str(manifest, "title"),
            pack_format=pack_format,
            domain_adapters={k: str(v) for k, v in adapters.items()},
            registry={role: RegistrySelection.parse(role, raw) for role, raw in registry.items()},
            manifest=manifest,
            files=tuple(entries),
        )

    def _definition(self, ref: PackRef, doc: JsonObject) -> Definition:
        return Definition(
            pack=ref,
            kind=_str(doc, "kind"),
            key=_str(doc, "key"),
            path=_str(doc, "path"),
            document_hash=_str(doc, "document_hash"),
            document=_obj(doc.get("document")),
        )

    def get_definition(self, ref: PackRef, kind: str, key: str) -> Definition:
        """One Definition by kind and id (path for ``layout`` / ``generation_record``).

        ``reference_solution`` and ``prompt`` are not Definitions here: use
        :meth:`get_reference_solution` and :meth:`get_prompt`.
        """
        if kind in ("reference_solution", "prompt"):
            raise ValueError(f"use the dedicated accessor for {kind!r}")
        full_key = ref.definition_key(kind, key)
        with self._store.transaction() as tx:
            doc = tx.get_projection(DEFINITION_PROJECTION, full_key)
        if doc is None:
            raise PackNotFoundError(full_key)
        return self._definition(ref, doc)

    def list_definitions(self, ref: PackRef, kind: str) -> list[Definition]:
        if kind in ("reference_solution", "prompt"):
            raise ValueError(f"use the dedicated accessor for {kind!r}")
        prefix = ref.definition_key(kind, "")
        with self._store.transaction() as tx:
            docs = tx.list_projection(DEFINITION_PROJECTION, key_prefix=prefix)
        return [self._definition(ref, doc) for _, doc in docs]

    def get_prompt(self, ref: PackRef, prompt_id: str, prompt_version: str) -> Prompt:
        full_key = ref.definition_key("prompt", f"{prompt_id}@{prompt_version}")
        with self._store.transaction() as tx:
            doc = tx.get_projection(DEFINITION_PROJECTION, full_key)
        if doc is None:
            raise PackNotFoundError(full_key)
        return Prompt(
            pack=ref,
            prompt_id=_str(doc, "prompt_id"),
            prompt_version=_str(doc, "prompt_version"),
            path=_str(doc, "path"),
            content_hash=_str(doc, "document_hash"),
            text=_str(doc, "text"),
        )

    def get_reference_solution(self, ref: PackRef, activity_id: str) -> ReferenceSolution:
        """The secret solution of ``activity_id``. For scoring and generation
        validation only: never pass it to learner or tutor-hint context."""
        full_key = ref.definition_key("reference_solution", activity_id)
        with self._store.transaction() as tx:
            doc = tx.get_projection(SECRET_PROJECTION, full_key)
        if doc is None:
            raise PackNotFoundError(full_key)
        document = _obj(doc.get("document"))
        return ReferenceSolution(
            pack=ref,
            solution_id=_str(document, "id"),
            activity_id=_str(document, "activity_id"),
            path=_str(doc, "path"),
            document_hash=_str(doc, "document_hash"),
            document=document,
        )
