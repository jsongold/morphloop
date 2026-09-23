"""Pack Importer (ADR-0015): pack source -> parse -> validate -> hash -> projection.

Steps, all before anything is written (a refused pack writes nothing):

1. list and read every file through the :class:`~harness.core.ports.PackSource` Port;
2. find exactly one root manifest (``manifest.json`` / ``.yaml`` / ``.yml``),
   parse it and validate it against ``pack/manifest.json``;
3. check the file index: every file except the manifest is indexed, every
   indexed file exists;
4. parse each indexed file (JSON/YAML; prompts are UTF-8 text) and validate it
   against the schema of its kind;
5. cross-file rules the schemas cannot express (``contracts/schemas/pack/README.md``,
   "Enforced by the Importer"): unique ids per kind, references resolve,
   visualization actors, document hash bindings, generation timings (v0.1
   accepts ``authoring`` only), registry prompts and output schemas resolve, and
   the selections of the roles the :class:`AlgorithmRegistry` governs are
   registered with valid parameters;
6. adapter references via :meth:`DomainAdapterRegistry.verify` (ADR-0009);
7. the pack content hash (ADR-0010, :func:`pack_content_hash`) over every file;
8. write the projections (:mod:`harness.core.pack.model`) in one transaction.

Every problem found in steps 3 to 6 is collected and reported together in
:class:`PackImportError`. Projections are keyed by pack id + version + content
hash and never overwritten: re-importing identical content is a no-op
(``created=False``); stored documents that differ from what this import would
write raise :class:`PackProjectionConflictError`. Dropping the three
projections and importing again rebuilds them (import order then follows the
re-import order). The pack document records ``import_seq``: its import order within
the pack_id, assigned once at first import; the highest is the latest import.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import DomainAdapterRegistry, ItemKind, ItemReference
from harness.core.pack.canonical_json import document_hash, pack_content_hash, sha256_hash
from harness.core.pack.model import (
    DEFINITION_PROJECTION,
    IMPORT_SEQ,
    KIND_SCHEMAS,
    MANIFEST_NAMES,
    MANIFEST_SCHEMA,
    PACK_PROJECTION,
    PROJECTION_FORMAT,
    SECRET_PROJECTION,
    PackRef,
    import_seq,
)
from harness.core.pack.parsing import PackParseError, parse_document, parse_text
from harness.core.ports import EventStore, PackSource, PlainJson, to_plain_json
from harness.core.registry.algorithms import (
    AlgorithmError,
    AlgorithmRegistry,
    RegistrySelection,
)

_V01_TIMING = "authoring"
_KINDS_WITHOUT_ID = frozenset({"layout", "generation_record"})

type Doc = dict[str, PlainJson]


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportProblem:
    """One reason a pack is refused. ``path`` is the pack file, when known."""

    code: str
    path: str | None
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.path or '<pack>'}: {self.message}"


class PackImportError(Exception):
    """The pack is refused; nothing was written."""

    def __init__(self, problems: Sequence[ImportProblem]) -> None:
        super().__init__(
            f"pack refused with {len(problems)} problem(s):\n" + "\n".join(str(p) for p in problems)
        )
        self.problems = tuple(problems)


class PackProjectionConflictError(Exception):
    """A projection for this pack key exists with different content."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedFile:
    path: str
    kind: str
    raw: bytes
    content: PlainJson  # parsed document, or the prompt text
    definition_key: str | None
    document_hash: str | None
    prompt: tuple[str, str] | None  # (prompt_id, prompt_version)


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedPack:
    """A validated pack, ready to be written (:meth:`PackImporter.check` result)."""

    ref: PackRef
    manifest_path: str
    manifest_raw: bytes
    manifest: Doc
    files: Sequence[ParsedFile]

    def projections(self) -> dict[tuple[str, str], Doc]:
        """Every projection document this pack writes, keyed by (name, key)."""
        ref = self.ref
        out: dict[tuple[str, str], Doc] = {}
        file_index: list[PlainJson] = [
            {
                "path": self.manifest_path,
                "kind": "manifest",
                "definition_key": None,
                "file_hash": sha256_hash(self.manifest_raw),
                "document_hash": document_hash(self.manifest),
            }
        ]
        for f in self.files:
            file_index.append(
                {
                    "path": f.path,
                    "kind": f.kind,
                    "definition_key": f.definition_key,
                    "file_hash": sha256_hash(f.raw),
                    "document_hash": f.document_hash,
                }
            )
            if f.kind == "reference_solution":
                assert isinstance(f.content, dict) and f.document_hash is not None
                activity_id = f.content["activity_id"]
                assert isinstance(activity_id, str)
                out[(SECRET_PROJECTION, ref.definition_key(f.kind, activity_id))] = {
                    "kind": f.kind,
                    "key": activity_id,
                    "path": f.path,
                    "document_hash": f.document_hash,
                    "document": f.content,
                }
                continue
            assert f.definition_key is not None
            doc: Doc = {
                "kind": f.kind,
                "key": f.definition_key,
                "path": f.path,
                "document_hash": f.document_hash if f.document_hash else sha256_hash(f.raw),
            }
            if f.prompt is not None:
                doc["prompt_id"], doc["prompt_version"] = f.prompt
                doc["text"] = f.content
            else:
                doc["document"] = f.content
            out[(DEFINITION_PROJECTION, ref.definition_key(f.kind, f.definition_key))] = doc
        return {
            (PACK_PROJECTION, ref.key): {
                "projection_format": PROJECTION_FORMAT,
                **ref.to_dict(),
                "manifest_path": self.manifest_path,
                "manifest": self.manifest,
                "files": file_index,
            },
            **out,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportResult:
    ref: PackRef
    created: bool


@dataclass
class _Problems:
    items: list[ImportProblem] = field(default_factory=list)

    def add(self, code: str, path: str | None, message: str) -> None:
        self.items.append(ImportProblem(code=code, path=path, message=message))

    def raise_if_any(self) -> None:
        if self.items:
            raise PackImportError(self.items)


def _obj(value: PlainJson) -> Doc:
    assert isinstance(value, dict)
    return value


def each_list(value: PlainJson | None) -> list[PlainJson]:
    return value if isinstance(value, list) else []


def _strs(value: PlainJson | None) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


class PackImporter:
    """Validates a pack and writes its projection. Only the Importer writes it."""

    def __init__(
        self,
        *,
        source: PackSource,
        store: EventStore,
        schemas: ContractSchemas,
        adapters: DomainAdapterRegistry,
        algorithms: AlgorithmRegistry,
    ) -> None:
        self._source = source
        self._store = store
        self._schemas = schemas
        self._adapters = adapters
        self._algorithms = algorithms

    # --- public API --------------------------------------------------------

    def check(self, location: str) -> ParsedPack:
        """Run steps 1-7 without writing. Raises :class:`PackImportError`."""
        paths = list(self._source.list_files(location))
        raw = {path: self._source.read_bytes(location, path) for path in paths}
        problems = _Problems()

        manifest_path, manifest = self._manifest(raw, problems)
        problems.raise_if_any()
        assert manifest_path is not None and manifest is not None

        index = _obj(manifest["files"])
        for path in paths:
            if path != manifest_path and path not in index:
                problems.add("file_not_indexed", path, "file is not in manifest.files")
        for path in index:
            if path not in raw:
                problems.add("indexed_file_missing", path, "indexed file does not exist")

        files: list[ParsedFile] = []
        for path, entry in sorted(index.items()):
            if path in raw:
                parsed = self._parse_file(path, _obj(entry), raw[path], problems)
                if parsed is not None:
                    files.append(parsed)
        problems.raise_if_any()

        self._cross_file(manifest, files, problems)
        self._adapter_references(manifest, files, problems)
        problems.raise_if_any()

        pack_id, pack_version = manifest["pack_id"], manifest["pack_version"]
        assert isinstance(pack_id, str) and isinstance(pack_version, str)
        ref = PackRef(
            pack_id=pack_id,
            pack_version=pack_version,
            content_hash=pack_content_hash(raw.items()),
        )
        return ParsedPack(
            ref=ref,
            manifest_path=manifest_path,
            manifest_raw=raw[manifest_path],
            manifest=manifest,
            files=files,
        )

    def import_pack(self, location: str) -> ImportResult:
        """Check the pack and write its projections (see the module docstring)."""
        parsed = self.check(location)
        documents: dict[tuple[str, str], PlainJson] = dict(parsed.projections())
        with self._store.transaction() as tx:
            existing = tx.get_projection(PACK_PROJECTION, parsed.ref.key)
            if existing is None:
                # Import order within a pack_id: the newest import is the one new
                # sessions start on. A re-import of an existing hash keeps its seq.
                # ponytail: concurrent imports of one pack_id may tie; the key breaks it.
                siblings = tx.list_projection(PACK_PROJECTION, key_prefix=parsed.ref.pack_id + "/")
                seq = 1 + max((import_seq(doc) for _, doc in siblings), default=0)
                for (name, key), document in parsed.projections().items():
                    if name == PACK_PROJECTION:
                        document = {**_obj(document), IMPORT_SEQ: seq}
                    tx.put_projection(name, key, document)
                return ImportResult(ref=parsed.ref, created=True)
            existing_pack = {k: v for k, v in existing.items() if k != IMPORT_SEQ}
            stored: dict[tuple[str, str], PlainJson] = {
                (PACK_PROJECTION, parsed.ref.key): to_plain_json(existing_pack)
            }
            for name in (DEFINITION_PROJECTION, SECRET_PROJECTION):
                for key, stored_doc in tx.list_projection(name, key_prefix=parsed.ref.key + "/"):
                    stored[(name, key)] = to_plain_json(stored_doc)
            if stored != documents:
                raise PackProjectionConflictError(
                    f"projection for {parsed.ref.key} exists with different content; "
                    "projections are never overwritten"
                )
            return ImportResult(ref=parsed.ref, created=False)

    # --- steps -------------------------------------------------------------

    def _manifest(
        self, raw: Mapping[str, bytes], problems: _Problems
    ) -> tuple[str | None, Doc | None]:
        found = [name for name in MANIFEST_NAMES if name in raw]
        if len(found) != 1:
            problems.add(
                "manifest",
                None,
                f"expected exactly one of {list(MANIFEST_NAMES)} at the pack root, found {found}",
            )
            return None, None
        path = found[0]
        try:
            manifest = parse_document(path, raw[path])
        except PackParseError as exc:
            problems.add("parse", path, str(exc))
            return None, None
        errors = self._schemas.errors(manifest, ContractSchemas.id_for(MANIFEST_SCHEMA))
        for error in errors:
            problems.add("schema", path, error)
        if errors:
            return None, None
        return path, _obj(manifest)

    def _parse_file(
        self, path: str, entry: Doc, data: bytes, problems: _Problems
    ) -> ParsedFile | None:
        kind = entry["kind"]
        assert isinstance(kind, str)
        try:
            if kind == "prompt":
                prompt_id, prompt_version = entry["prompt_id"], entry["prompt_version"]
                assert isinstance(prompt_id, str) and isinstance(prompt_version, str)
                return ParsedFile(
                    path=path,
                    kind=kind,
                    raw=data,
                    content=parse_text(path, data),
                    definition_key=f"{prompt_id}@{prompt_version}",
                    document_hash=None,
                    prompt=(prompt_id, prompt_version),
                )
            content = parse_document(path, data)
        except PackParseError as exc:
            problems.add("parse", path, str(exc))
            return None
        schema = KIND_SCHEMAS[kind]
        assert schema is not None
        errors = self._schemas.errors(content, ContractSchemas.id_for(schema))
        for error in errors:
            problems.add("schema", path, error)
        if errors:
            return None
        key: str | None
        if kind in _KINDS_WITHOUT_ID:
            key = path
        else:
            doc_id = _obj(content)["id"]
            assert isinstance(doc_id, str)
            key = doc_id
        return ParsedFile(
            path=path,
            kind=kind,
            raw=data,
            content=content,
            definition_key=key,
            document_hash=document_hash(content),
            prompt=None,
        )

    def _cross_file(self, manifest: Doc, files: Sequence[ParsedFile], problems: _Problems) -> None:
        by_kind: dict[str, dict[str, ParsedFile]] = {}
        by_path = {f.path: f for f in files}
        for f in files:
            assert f.definition_key is not None
            bucket = by_kind.setdefault(f.kind, {})
            if f.definition_key in bucket:
                problems.add(
                    "duplicate_id",
                    f.path,
                    f"{f.kind} {f.definition_key!r} is also defined in "
                    f"{bucket[f.definition_key].path}",
                )
            bucket[f.definition_key] = f

        def exists(kind: str, ident: str, path: str, where: str) -> None:
            if ident not in by_kind.get(kind, {}):
                problems.add("unresolved_reference", path, f"{where}: no {kind} {ident!r}")

        def docs(kind: str) -> Iterator[tuple[ParsedFile, Doc]]:
            for f in by_kind.get(kind, {}).values():
                yield f, _obj(f.content)

        def remediation(doc: Doc, path: str) -> None:
            rem = doc.get("remediation")
            if isinstance(rem, dict):
                for ident in _strs(rem.get("references")):
                    exists("reference", ident, path, "remediation.references")
                for ident in _strs(rem.get("visualizations")):
                    exists("visualization", ident, path, "remediation.visualizations")

        def skills(targets: PlainJson | None, path: str, where: str) -> None:
            if isinstance(targets, dict):
                for group in ("primary", "secondary"):
                    for ident in _strs(targets.get(group)):
                        exists("skill", ident, path, f"{where}.{group}")
            else:
                for ident in _strs(targets):
                    exists("skill", ident, path, where)

        for f, doc in docs("skill"):
            skills(doc.get("prerequisites"), f.path, "prerequisites")
        for kind in ("visualization", "reference"):
            for f, doc in docs(kind):
                skills(doc.get("skills"), f.path, "skills")

        solutions_for: dict[str, str] = {}
        for f, doc in docs("reference_solution"):
            activity_id = doc["activity_id"]
            assert isinstance(activity_id, str)
            exists("activity", activity_id, f.path, "activity_id")
            if activity_id in solutions_for:
                problems.add(
                    "duplicate_id",
                    f.path,
                    f"activity {activity_id!r} already has a solution in "
                    f"{solutions_for[activity_id]}",
                )
            solutions_for[activity_id] = f.path

        for f, doc in docs("activity"):
            skills(doc.get("skills"), f.path, "skills")
            exists("evaluator", str(doc["evaluator"]), f.path, "evaluator")
            if "environment" in doc:
                exists("environment", str(doc["environment"]), f.path, "environment")
            remediation(doc, f.path)
            # A visualization's commands must target this activity's real lab (AC-C2/C3).
            env_file = by_kind.get("environment", {}).get(str(doc.get("environment")))
            env_params = _obj(_obj(env_file.content)["params"]) if env_file else {}
            rem = doc.get("remediation")
            viz_ids = _strs(rem.get("visualizations")) if isinstance(rem, dict) else []
            for ident in viz_ids:
                viz = by_kind.get("visualization", {}).get(ident)
                bindings = _obj(viz.content).get("environment_bindings") if viz else None
                if not isinstance(bindings, dict):
                    continue
                for name, value in bindings.items():
                    if env_params.get(name) != value:
                        problems.add(
                            "binding_mismatch",
                            f.path,
                            f"remediation.visualizations: {ident!r} shows {name}={value!r}, "
                            f"the activity's environment has {env_params.get(name)!r}",
                        )
            origin = doc.get("origin")
            if isinstance(origin, dict) and origin.get("type") == "generated":
                exists("activity_template", str(origin.get("template_id")), f.path, "origin")
                if origin.get("timing") != _V01_TIMING:
                    problems.add(
                        "timing_not_supported", f.path, "v0.1 imports 'authoring' content only"
                    )
            sol = doc.get("reference_solution")
            if isinstance(sol, dict):
                target = by_path.get(str(sol["path"]))
                if target is None or target.kind != "reference_solution":
                    problems.add(
                        "unresolved_reference",
                        f.path,
                        f"reference_solution: {sol['path']!r} is not a reference_solution file",
                    )
                else:
                    if sol["content_hash"] != target.document_hash:
                        problems.add(
                            "hash_mismatch",
                            f.path,
                            f"reference_solution hash {sol['content_hash']} != "
                            f"{target.document_hash} of {target.path}",
                        )
                    if _obj(target.content)["activity_id"] != doc["id"]:
                        problems.add(
                            "unresolved_reference",
                            f.path,
                            f"{target.path} is the solution of another activity",
                        )

        for f, doc in docs("visualization"):
            diagram = _obj(doc["diagram"])
            actors = {a["id"] for a in each_list(diagram["actors"]) if isinstance(a, dict)}
            steps = diagram["steps"]
            assert isinstance(steps, list)
            for i, step in enumerate(steps):
                step = _obj(step)
                for end in ("from", "to"):
                    if step.get(end) not in actors:
                        problems.add(
                            "unresolved_reference",
                            f.path,
                            f"diagram.steps[{i}].{end}: {step.get(end)!r} is not a declared actor",
                        )

        for f, doc in docs("layout"):
            toc = doc.get("toc")
            chapters = _obj(toc)["chapters"] if isinstance(toc, dict) else []
            for i, chapter in enumerate(each_list(chapters)):
                for j, item in enumerate(each_list(_obj(chapter)["items"])):
                    item = _obj(item)
                    where = f"toc.chapters[{i}].items[{j}]"
                    exists(str(item["kind"]), str(item["id"]), f.path, where)

        generation = manifest.get("content_generation")
        default_timing = _obj(generation)["default_timing"] if generation is not None else None
        if default_timing is not None and default_timing != _V01_TIMING:
            problems.add(
                "timing_not_supported",
                None,
                f"content_generation.default_timing {default_timing!r}: v0.1 implements "
                "'authoring' only",
            )
        for f, doc in docs("activity_template"):
            skills(doc.get("skills"), f.path, "skills")
            exists("evaluator", str(doc["evaluator"]), f.path, "evaluator")
            remediation(doc, f.path)
            timing = doc.get("timing", default_timing)
            if timing != _V01_TIMING:
                code = "holdout_not_authoring" if doc.get("holdout") else "timing_not_supported"
                problems.add(code, f.path, f"effective timing {timing!r} is not 'authoring'")

        for f, doc in docs("generation_record"):
            template = _obj(doc["template"])
            tmpl = by_kind.get("activity_template", {}).get(str(template["template_id"]))
            if tmpl is None:
                exists("activity_template", str(template["template_id"]), f.path, "template")
            elif tmpl.document_hash != template["template_hash"]:
                problems.add("hash_mismatch", f.path, f"template hash != that of {tmpl.path}")
            outputs = doc["outputs"]
            assert isinstance(outputs, list)
            for i, output in enumerate(outputs):
                output = _obj(output)
                target = by_path.get(str(output["path"]))
                where = f"outputs[{i}]"
                if target is None or target.kind != output["kind"]:
                    problems.add(
                        "unresolved_reference",
                        f.path,
                        f"{where}: {output['path']!r} is not a {output['kind']} file",
                    )
                    continue
                if _obj(target.content).get("id") != output["definition_id"]:
                    problems.add("unresolved_reference", f.path, f"{where}: id mismatch")
                if target.document_hash != output["definition_hash"]:
                    problems.add("hash_mismatch", f.path, f"{where}: hash != that of {target.path}")

        prompts = {f.prompt for f in files if f.prompt is not None}
        registry = _obj(manifest["registry"])
        governed = self._algorithms.governed_roles()
        for role, raw_selection in sorted(registry.items()):
            try:
                selection = RegistrySelection.parse(role, raw_selection)
            except AlgorithmError as exc:
                problems.add("registry", None, str(exc))
                continue
            llm = selection.llm
            if (llm.prompt_id, llm.prompt_version) not in prompts:
                problems.add(
                    "unresolved_reference",
                    None,
                    f"registry.{role}.llm: no prompt {llm.prompt_id!r} "
                    f"version {llm.prompt_version!r}",
                )
            if selection.output_schema is not None and not self._schemas.has(
                selection.output_schema
            ):
                problems.add(
                    "unresolved_reference",
                    None,
                    f"registry.{role}.output_schema {selection.output_schema!r} "
                    "is not in contracts/",
                )
            if role in governed:
                try:
                    self._algorithms.validate(selection, self._schemas)
                except AlgorithmError as exc:
                    problems.add("registry", None, str(exc))

    def _adapter_references(
        self, manifest: Doc, files: Sequence[ParsedFile], problems: _Problems
    ) -> None:
        refs: list[ItemReference] = []

        def add(kind: ItemKind, item_id: PlainJson, params: PlainJson | None, source: str) -> None:
            assert isinstance(item_id, str)
            assert params is None or isinstance(params, dict)
            refs.append(ItemReference(kind=kind, item_id=item_id, params=params, source=source))

        def each(value: PlainJson) -> list[PlainJson]:
            return value if isinstance(value, list) else []

        for f in files:
            if f.prompt is not None:
                continue
            doc = _obj(f.content)
            if f.kind == "environment":
                add("fixture", doc["fixture"], doc["params"], f"{f.path}#/fixture")
            elif f.kind == "activity":
                for i, check in enumerate(each(doc["checks"])):
                    check = _obj(check)
                    add("check", check["check"], check.get("params"), f"{f.path}#/checks/{i}")
                for i, tool in enumerate(each(doc["tools"])):
                    add("tool", tool, None, f"{f.path}#/tools/{i}")
            elif f.kind == "activity_template":
                for i, fixture in enumerate(each(doc["allowed_fixtures"])):
                    add("fixture", fixture, None, f"{f.path}#/allowed_fixtures/{i}")
                for i, check in enumerate(each(doc["allowed_checks"])):
                    add("check", check, None, f"{f.path}#/allowed_checks/{i}")
                for i, tool in enumerate(each(doc["tools"])):
                    add("tool", tool, None, f"{f.path}#/tools/{i}")
        requirements = _obj(manifest["domain_adapters"])
        declared = {k: v for k, v in requirements.items() if isinstance(v, str)}
        for problem in self._adapters.verify(declared, refs):
            path = problem.source.split("#", 1)[0] if problem.source else None
            problems.add(f"adapter:{problem.code}", path, problem.message)
