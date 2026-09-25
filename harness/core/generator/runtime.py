"""Runtime pre-generation of drill items and textbook docs (#34, #64; ADR-0014 revised).

One call asks the LLM (through the :class:`LLMProvider` Port) for one candidate
shaped like the pack v2 schema, validates it, and stores it in the
:class:`GeneratedDocumentStore` only when it passes. A rejected candidate or a
failed LLM call stores nothing and returns nothing: generated content is never
shown before it is validated and stored immutably.

Validation:

- the pack v2 schema of the resource (``drill-item.json`` / ``textbook-doc.json``);
- labels: pack vocabulary or ``topic:<id>`` only. ``sys:holdout`` and ``origin:*``
  are the SDK's; every stored document gets ``origin:generated``;
- a ``choice`` item's ``expected`` is one of its ``choices``; an ``artifact`` item
  names a (non-holdout) pack artifact spec;
- a lab variant (the drill output's ``lab``, a ``generator.activity_candidate``)
  goes through :class:`CandidateValidator`: only the lab spec's
  ``allowed_fixtures`` / ``allowed_checks``, checks fail broken and pass after the
  reference solution. It is stored as a new ``artifact`` document the item points to.

LLM settings and prompt are the pack's ``generator`` role (``llm_roles``); the prompt
version recorded is the pack hash. Ids are assigned here (lowercase UUIDs), never
taken from the LLM. ``generate`` is synchronous like the Port; run it in the background with FastAPI
``BackgroundTasks`` (sync callables go to the thread pool) or ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from harness.core.contract_schemas import ContractSchemas
from harness.core.generator.validate import (
    CANDIDATE_SCHEMA_ID,
    SCHEMA_STEP,
    Candidate,
    CandidateValidator,
    Document,
    Paths,
    Rejected,
)
from harness.core.labels import HOLDOUT, label_problems
from harness.core.pack.v2.importer import PackV2
from harness.core.ports import (
    JsonObject,
    JsonValue,
    LLMError,
    LLMMessage,
    LLMProvenance,
    LLMProvider,
    LLMRequest,
    PlainJson,
    to_plain_json,
)
from harness.core.ports.generated_documents import GeneratedDocument, GeneratedDocumentStore

logger = logging.getLogger(__name__)

type GeneratedResource = Literal["drill", "textbook"]

ORIGIN_GENERATED = "origin:generated"
DRILL_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/v2/drill-item.json")
TEXTBOOK_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/v2/textbook-doc.json")
ARTIFACT_SCHEMA_ID = ContractSchemas.id_for("schemas/pack/v2/artifact-spec.json")

LABEL_STEP = "labels"
REFERENCE_STEP = "references"


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateRequest:
    """What to generate from. Memo entries and the gap are opaque JSON objects."""

    resource: GeneratedResource
    memo_entries: Sequence[JsonObject]
    gap: JsonObject | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LabValidation:
    """Lab-backed validation; the timeout is the caller's (the harness has no default)."""

    validator: CandidateValidator
    solution_step_timeout_seconds: int


def _plain(value: JsonValue) -> Document:
    plain = to_plain_json(value)
    assert isinstance(plain, dict)
    return plain


def _strings(value: PlainJson | None) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _new_id() -> str:
    return str(uuid4())


class PreGenerator:
    """Generates, validates and stores one document per :meth:`generate` call."""

    def __init__(
        self,
        *,
        pack: PackV2,
        llm: LLMProvider,
        provider: str,
        schemas: ContractSchemas,
        store: GeneratedDocumentStore,
        lab: LabValidation | None = None,
        new_id: Callable[[], str] = _new_id,
    ) -> None:
        self._pack = pack
        self._llm = llm
        role = pack.llm_roles.get("generator")
        if role is None:
            raise ValueError("the pack declares no 'generator' LLM role")
        parameters = {"temperature": role.temperature, "max_tokens": role.max_tokens}
        self._llm_settings = LLMProvenance(
            provider=provider,
            model=role.model,
            prompt_id=role.prompt,
            prompt_version=pack.pack_hash,
            generation_parameters={k: v for k, v in parameters.items() if v is not None},
        )
        self._prompt = role.prompt_text
        self._schemas = schemas
        self._store = store
        self._lab = lab
        self._new_id = new_id
        self._topic_ids = frozenset(pack.topic_ids)
        specs = [_plain(doc) for doc in pack.documents["artifacts"].values()]
        # Holdout artifacts are never shown to, or varied by, the generator.
        self._artifacts = {
            str(s["id"]): s for s in specs if HOLDOUT not in _strings(s.get("labels"))
        }

    def output_schema(self, resource: GeneratedResource) -> Document:
        if resource == "textbook":
            return self._schemas.bundle(TEXTBOOK_SCHEMA_ID)
        return {
            "type": "object",
            "required": ["item", "lab"],
            "properties": {
                "item": self._schemas.bundle(DRILL_SCHEMA_ID),
                "lab": {"anyOf": [self._schemas.bundle(CANDIDATE_SCHEMA_ID), {"type": "null"}]},
            },
            "additionalProperties": False,
        }

    def build_request(self, request: GenerateRequest) -> LLMRequest:
        context = {
            "resource": request.resource,
            "memo_entries": [_plain(e) for e in request.memo_entries],
            "gap": None if request.gap is None else _plain(request.gap),
            "labels": sorted(self._pack.labels),
            "topic_ids": sorted(self._topic_ids),
            "artifacts": list(self._artifacts.values()),
        }
        return LLMRequest(
            role="generator",
            llm=self._llm_settings,
            messages=(
                LLMMessage(role="system", content=self._prompt),
                LLMMessage(role="user", content=json.dumps(context, ensure_ascii=False)),
            ),
            output_schema_id=(
                TEXTBOOK_SCHEMA_ID if request.resource == "textbook" else DRILL_SCHEMA_ID
            ),
            output_schema=self.output_schema(request.resource),
        )

    def generate(self, request: GenerateRequest) -> tuple[GeneratedDocument, ...]:
        """Store and return the validated documents; ``()`` when nothing passed."""
        try:
            response = self._llm.complete_structured(self.build_request(request))
            output = _plain(response.output)
            provenance: Document = {
                "llm": response.provenance.to_dict(),
                "pack_id": self._pack.pack_id,
                "pack_hash": self._pack.pack_hash,
            }
            documents: tuple[GeneratedDocument, ...]
            if request.resource == "textbook":
                documents = (self._textbook(output, provenance),)
            else:
                documents = self._drill(output, provenance, response.provenance)
        except (LLMError, Rejected) as exc:
            logger.warning("pre-generation of a %s stored nothing: %s", request.resource, exc)
            return ()
        # ponytail: one add per document, not one transaction; the artifact goes first so a
        # stored item never points at a missing artifact. One transaction if the store grows one.
        for document in documents:
            self._store.add(document)
        return documents

    # --- per resource ---------------------------------------------------------

    def _textbook(self, output: Document, provenance: Document) -> GeneratedDocument:
        doc = {**output, "id": self._new_id()}
        self._check_schema(doc, TEXTBOOK_SCHEMA_ID)
        blocks = [b for b in doc["blocks"] if isinstance(b, dict)]  # type: ignore[union-attr]
        block_ids = [str(b["id"]) for b in blocks]
        if len(set(block_ids)) != len(block_ids):
            raise Rejected(SCHEMA_STEP, f"block ids are not unique: {block_ids}")
        for block in blocks:
            self._check_labels(_strings(block["labels"]), f"block {block['id']}")
        return self._finalize("textbook", doc, provenance)

    def _drill(
        self, output: Document, provenance: Document, llm: LLMProvenance
    ) -> tuple[GeneratedDocument, ...]:
        item_output = output.get("item")
        if not isinstance(item_output, dict):
            raise Rejected(SCHEMA_STEP, "the output has no drill item")
        item: Document = {**item_output, "id": self._new_id()}
        self._check_schema(item, DRILL_SCHEMA_ID)
        mode = item["answer_mode"]
        if mode == "choice" and item["expected"] not in _strings(item.get("choices")):
            raise Rejected(REFERENCE_STEP, "'expected' is not one of 'choices'")
        lab_output = output.get("lab")
        documents: list[GeneratedDocument] = []
        if mode == "artifact":
            spec = self._artifacts.get(str(item["artifact_ref"]))
            if spec is None:
                raise Rejected(REFERENCE_STEP, f"no pack artifact {item['artifact_ref']!r}")
            if isinstance(lab_output, dict):
                artifact = self._lab_variant(spec, lab_output, provenance, llm)
                item["artifact_ref"] = artifact.id
                documents.append(artifact)
        if lab_output is not None and not documents:
            raise Rejected(REFERENCE_STEP, "a lab variant needs an 'artifact' item")
        documents.append(self._finalize("drill", item, provenance))
        return tuple(documents)

    def _lab_variant(
        self, spec: Document, candidate: Document, provenance: Document, llm: LLMProvenance
    ) -> GeneratedDocument:
        if self._lab is None:
            raise Rejected(REFERENCE_STEP, "no lab validation is wired, so no lab variant")
        if spec["type"] != "lab":
            raise Rejected(REFERENCE_STEP, f"artifact {spec['id']!r} is not a lab")
        lab_spec = spec["spec"]
        assert isinstance(lab_spec, dict)
        environment = lab_spec["environment"]
        assert isinstance(environment, dict)
        artifact_id = self._new_id()
        # The CandidateValidator merges a v0.1 Template; the lab spec supplies its bounds.
        template: Document = {
            "id": spec["id"],
            "activity_type": "drill",
            "skills": {"primary": [spec["id"]]},
            "evaluator": spec["id"],
            "image": environment["image"],
            "allowed_fixtures": lab_spec["allowed_fixtures"],
            "allowed_checks": lab_spec["allowed_checks"],
            "tools": [],
            "solution_step_timeout_seconds": self._lab.solution_step_timeout_seconds,
        }
        where = f"generated/{artifact_id}"
        accepted = self._lab.validator.validate(
            candidate=Candidate(output=candidate, provenance=llm),
            manifest=_plain(self._pack.manifest),
            template=template,
            activity_id=artifact_id,
            paths=Paths(
                activity=f"{where}/activity.json",
                environment=f"{where}/environment.json",
                solution=f"{where}/solution.json",
                record=f"{where}/record.json",
            ),
            visualizations={},
        )
        document: Document = {
            "id": artifact_id,
            "type": "lab",
            "labels": spec["labels"],
            "spec": {
                "environment": accepted.environment,
                "allowed_fixtures": lab_spec["allowed_fixtures"],
                "allowed_checks": lab_spec["allowed_checks"],
            },
        }
        # The checks and the secret reference solution stay in provenance, never in the body.
        lab_provenance: Document = {
            **provenance,
            "source_artifact": spec["id"],
            "checks": accepted.activity["checks"],
            "reference_solution": accepted.solution,
            "validation": list(accepted.steps),
        }
        return self._finalize("artifact", document, lab_provenance)

    # --- shared checks --------------------------------------------------------

    def _check_schema(self, document: Document, schema_id: str) -> None:
        errors = self._schemas.errors(document, schema_id)
        if errors:
            raise Rejected(SCHEMA_STEP, "; ".join(errors))

    def _check_labels(self, labels: Iterable[str], where: str) -> None:
        labels = list(labels)
        problems = {
            label: "reserved for the SDK"
            for label in labels
            if label == HOLDOUT or label.startswith("origin:")
        }
        problems |= label_problems(
            [label for label in labels if label not in problems],
            vocabulary=self._pack.labels,
            topic_ids=self._topic_ids,
        )
        if problems:
            details = "; ".join(f"{label!r}: {why}" for label, why in sorted(problems.items()))
            raise Rejected(LABEL_STEP, f"{where}: {details}")

    def _finalize(
        self, resource: str, body: Document, provenance: Mapping[str, PlainJson]
    ) -> GeneratedDocument:
        labels = _strings(body["labels"])
        if resource != "artifact":
            self._check_labels(labels, resource)
        body = {**body, "labels": [*labels, ORIGIN_GENERATED]}
        self._check_schema(
            body,
            {"drill": DRILL_SCHEMA_ID, "textbook": TEXTBOOK_SCHEMA_ID}.get(
                resource, ARTIFACT_SCHEMA_ID
            ),
        )
        return GeneratedDocument(
            resource=resource,
            id=str(body["id"]),
            body=body,
            labels=tuple(_strings(body["labels"])),
            provenance=dict(provenance),
        )
