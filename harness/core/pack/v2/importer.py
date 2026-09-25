"""Pack v2 Importer (#34, #53): pack directory -> validated, immutable :class:`PackV2`.

Steps (every problem of a step is reported together in :class:`PackV2ImportError`):

1. read every file under the pack directory (a symlink is a problem);
2. parse and schema-validate the root manifest (``pack/v2/manifest.json``);
3. every file is listed in the manifest and every listed file exists;
4. parse each listed file and validate it against the schema of its kind;
5. resolve each ``llm_roles`` file's ``prompt`` Markdown path (checked for
   existence here, since it is a plain-text path referenced from inside a
   JSON document rather than listed directly under a manifest key);
6. validate each artifact's ``spec`` with its type's ``spec_schema`` and
   ``validate_spec`` (:mod:`harness.core.artifact`); an unregistered ``type``
   is a problem. The types come from the caller (an app passes the ones it
   registers); the process-wide subclass registry is only the default (#95);
7. run every registered validator (:mod:`harness.core.pack.v2.validators`).

The pack hash is the v1 pack content hash (ADR-0010) over every file. Nothing is
stored here; the result is a value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from harness.core.artifact import Artifact, registered_artifact_types
from harness.core.contract_schemas import ContractSchemas
from harness.core.pack.canonical_json import pack_content_hash
from harness.core.pack.model import MANIFEST_NAMES
from harness.core.pack.parsing import PackParseError, parse_document, parse_text
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
        "llm_roles": _V2 + "llm-role.json",
    }
)
"""Manifest key -> schema of the files it lists (``labels`` lists one file)."""


class PackV2ImportError(Exception):
    """The pack is refused; ``problems`` lists every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(f"pack refused with {len(problems)} problem(s):\n" + "\n".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True, kw_only=True)
class LLMRole:
    """One ``llm_roles`` declaration plus the text of its prompt file.

    Tuning values (``model``, ``temperature``, ``max_tokens``) are exactly what
    the pack declares; the harness adds no defaults (ADR-0002). ``role`` is
    pack vocabulary, not an SDK enum (ADR-0018).
    """

    role: str
    model: str
    temperature: float | None
    max_tokens: int | None
    prompt: str
    prompt_text: str
    output_schema: str | None


def _optional_number(doc: JsonObject, key: str) -> float | None:
    value = doc.get(key)
    if value is None:
        return None
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _optional_int(doc: JsonObject, key: str) -> int | None:
    value = doc.get(key)
    if value is None:
        return None
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


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
    llm_roles: Mapping[str, LLMRole]

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


def _artifact_types(types: Iterable[type[Artifact]] | None) -> Mapping[str, type[Artifact]]:
    return registered_artifact_types() if types is None else {cls.type: cls for cls in types}


def _artifact_problems(
    pack: PackV2, types: Mapping[str, type[Artifact]], schemas: ContractSchemas
) -> list[str]:
    """Each artifact's ``spec`` against its type's schema, then its validator."""
    problems: list[str] = []
    for path, doc in pack.documents["artifacts"].items():
        type_name = str(doc["type"])
        cls = types.get(type_name)
        if cls is None:
            known = ", ".join(sorted(types)) or "none"
            problems.append(f"{path}: artifact type {type_name!r} is not registered ({known})")
            continue
        spec = doc["spec"]
        assert isinstance(spec, Mapping)
        errors = schemas.errors_against(spec, cls.spec_schema)
        if errors:
            problems.extend(f"{path}: $.spec{e[1:]}" for e in errors)
        else:
            problems.extend(f"{path}: {p}" for p in cls.validate_spec(spec, pack))
    return problems


def import_pack_v2(
    path: Path | str,
    *,
    schemas: ContractSchemas | None = None,
    artifact_types: Iterable[type[Artifact]] | None = None,
) -> PackV2:
    """Read, validate and hash the v2 pack at ``path``. Raises :class:`PackV2ImportError`.

    ``artifact_types`` are the :class:`Artifact` subclasses the pack may use; an
    artifact of any other ``type`` is a problem. ``None`` means every subclass
    defined in this process (:func:`registered_artifact_types`).
    """
    root = Path(path)
    if not root.is_dir():
        raise PackV2ImportError([f"{root}: not a pack directory"])
    schemas = schemas or ContractSchemas.load()
    types = _artifact_types(artifact_types)
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

    # A role's prompt is a Markdown file referenced by path from within its JSON
    # config, not listed directly under a manifest key: resolve it here so the
    # "every file is listed" check below accounts for it.
    prompt_text: dict[str, str] = {}
    for role_path, role_doc in documents["llm_roles"].items():
        prompt_path = str(role_doc["prompt"])
        if prompt_path not in raw:
            problems.append(f"{role_path}: prompt file {prompt_path!r} does not exist")
            continue
        try:
            prompt_text[prompt_path] = parse_text(prompt_path, raw[prompt_path])
        except PackParseError as exc:
            problems.append(str(exc))

    all_listed = {p for paths in listed.values() for p in paths} | set(prompt_text)
    problems.extend(
        f"{p}: file is not listed in the manifest"
        for p in raw
        if p != manifest_path and p not in all_listed
    )
    if problems:
        raise PackV2ImportError(problems)

    vocabulary = next(iter(documents["labels"].values()))["labels"]
    assert isinstance(vocabulary, Sequence)
    llm_roles = {
        str(doc["role"]): LLMRole(
            role=str(doc["role"]),
            model=str(doc["model"]),
            temperature=_optional_number(doc, "temperature"),
            max_tokens=_optional_int(doc, "max_tokens"),
            prompt=str(doc["prompt"]),
            prompt_text=prompt_text[str(doc["prompt"])],
            output_schema=str(doc["output_schema"]) if "output_schema" in doc else None,
        )
        for doc in documents["llm_roles"].values()
    }
    pack = PackV2(
        pack_id=str(manifest["pack_id"]),
        pack_version=str(manifest["pack_version"]),
        pack_hash=pack_content_hash(raw.items()),
        manifest=MappingProxyType({k: _freeze(v) for k, v in manifest.items()}),
        labels=frozenset(str(label) for label in vocabulary),
        topics=tuple(documents["topics"].values()),
        documents=MappingProxyType({k: MappingProxyType(v) for k, v in documents.items()}),
        llm_roles=MappingProxyType(llm_roles),
    )
    problems.extend(_artifact_problems(pack, types, schemas))
    for name, validate in validators.registered().items():
        problems.extend(f"[{name}] {problem}" for problem in validate(pack))
    if problems:
        raise PackV2ImportError(problems)
    return pack
