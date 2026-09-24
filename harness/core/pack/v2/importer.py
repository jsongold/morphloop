"""Pack v2 Importer (#34, #53): pack directory -> validated, immutable :class:`PackV2`.

Steps (every problem of a step is reported together in :class:`PackV2ImportError`):

1. read every file under the pack directory (a symlink is a problem);
2. parse and schema-validate the root manifest (``pack/v2/manifest.json``);
3. every file is listed in the manifest and every listed file exists;
4. parse each listed file and validate it against the schema of its kind;
5. run every registered validator (:mod:`harness.core.pack.v2.validators`).

The pack hash is the v1 pack content hash (ADR-0010) over every file. Nothing is
stored here; the result is a value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.canonical_json import pack_content_hash
from harness.core.pack.model import MANIFEST_NAMES
from harness.core.pack.parsing import PackParseError, parse_document
from harness.core.pack.v2 import validators
from harness.core.ports import JsonObject, JsonValue, PlainJson

_V2 = "schemas/pack/v2/"
MANIFEST_SCHEMA = _V2 + "manifest.json"
KIND_SCHEMAS: Mapping[str, str] = MappingProxyType(
    {
        "labels": _V2 + "labels.json",
        "topics": _V2 + "topic.json",
        "textbooks": _V2 + "textbook-doc.json",
        "drills": _V2 + "drill-item.json",
        "artifacts": _V2 + "artifact-spec.json",
    }
)
"""Manifest key -> schema of the files it lists (``labels`` lists one file)."""


class PackV2ImportError(Exception):
    """The pack is refused; ``problems`` lists every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(f"pack refused with {len(problems)} problem(s):\n" + "\n".join(problems))
        self.problems = tuple(problems)


def _freeze(value: PlainJson) -> JsonValue:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class PackV2:
    """A validated v2 pack. ``documents`` is manifest key -> {path: parsed file}."""

    pack_id: str
    pack_version: str
    pack_hash: str
    manifest: JsonObject
    labels: frozenset[str]
    topics: tuple[JsonObject, ...]
    documents: Mapping[str, Mapping[str, JsonObject]]

    @property
    def topic_ids(self) -> tuple[str, ...]:
        """Every topic id in tree order (depth first), duplicates kept."""
        out: list[str] = []
        stack = list(reversed(self.topics))
        while stack:
            topic = stack.pop()
            out.append(str(topic["id"]))
            children = topic.get("topics", ())
            assert isinstance(children, Sequence)
            stack.extend(c for c in reversed(children) if isinstance(c, Mapping))
        return tuple(out)


def _read(root: Path, problems: list[str]) -> dict[str, bytes]:
    raw: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            problems.append(f"{rel}: symlinks are not allowed in a pack")
        elif path.is_file():
            raw[rel] = path.read_bytes()
    return raw


def _parse(
    path: str, data: bytes, schema: str, schemas: ContractSchemas, problems: list[str]
) -> PlainJson:
    """Parsed, schema-valid document, or ``None`` after recording the problems."""
    try:
        doc = parse_document(path, data)
    except PackParseError as exc:
        problems.append(str(exc))
        return None
    errors = schemas.errors(doc, ContractSchemas.id_for(schema))
    problems.extend(f"{path}: {e}" for e in errors)
    return None if errors else doc


def _listed(manifest: dict[str, PlainJson]) -> dict[str, list[str]]:
    """Manifest key -> listed paths."""
    out: dict[str, list[str]] = {}
    for kind in KIND_SCHEMAS:
        value = manifest[kind]
        # schema-valid: ``labels`` is one path, every other kind a list of paths
        paths = value if isinstance(value, list) else [value]
        out[kind] = [str(p) for p in paths]
    return out


def import_pack_v2(path: Path | str, *, schemas: ContractSchemas | None = None) -> PackV2:
    """Read, validate and hash the v2 pack at ``path``. Raises :class:`PackV2ImportError`."""
    root = Path(path)
    if not root.is_dir():
        raise PackV2ImportError([f"{root}: not a pack directory"])
    schemas = schemas or ContractSchemas.load()
    problems: list[str] = []
    raw = _read(root, problems)

    found = [name for name in MANIFEST_NAMES if name in raw]
    if len(found) != 1:
        problems.append(f"<pack>: expected exactly one of {list(MANIFEST_NAMES)}, found {found}")
        raise PackV2ImportError(problems)
    manifest_path = found[0]
    manifest = _parse(manifest_path, raw[manifest_path], MANIFEST_SCHEMA, schemas, problems)
    if not isinstance(manifest, dict):
        raise PackV2ImportError(problems)

    listed = _listed(manifest)
    all_listed = {p for paths in listed.values() for p in paths}
    problems.extend(
        f"{p}: file is not listed in the manifest"
        for p in raw
        if p != manifest_path and p not in all_listed
    )
    documents: dict[str, dict[str, JsonObject]] = {}
    for kind, paths in listed.items():
        documents[kind] = {}
        for p in paths:
            if p not in raw:
                problems.append(f"{p}: listed under {kind!r} but does not exist")
                continue
            doc = _parse(p, raw[p], KIND_SCHEMAS[kind], schemas, problems)
            if isinstance(doc, dict):
                frozen = _freeze(doc)
                assert isinstance(frozen, Mapping)
                documents[kind][p] = frozen
    if problems:
        raise PackV2ImportError(problems)

    vocabulary = next(iter(documents["labels"].values()))["labels"]
    assert isinstance(vocabulary, Sequence)
    pack = PackV2(
        pack_id=str(manifest["pack_id"]),
        pack_version=str(manifest["pack_version"]),
        pack_hash=pack_content_hash(raw.items()),
        manifest=MappingProxyType({k: _freeze(v) for k, v in manifest.items()}),
        labels=frozenset(str(label) for label in vocabulary),
        topics=tuple(documents["topics"].values()),
        documents=MappingProxyType({k: MappingProxyType(v) for k, v in documents.items()}),
    )
    for name, validate in validators.registered().items():
        problems.extend(f"[{name}] {problem}" for problem in validate(pack))
    if problems:
        raise PackV2ImportError(problems)
    return pack
