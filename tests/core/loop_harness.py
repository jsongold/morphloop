"""A hermetic pack plus wired fakes for the learning-loop tests.

The pack mirrors the shape of ``contents/software-engineering`` (one skill,
one lab-backed activity with a reference solution, an evaluator rubric, a
visualization, a reference, a layout and the three prompts) but names items of
the fake domain adapter, so nothing here depends on a real domain adapter or on
files under ``contents/``. Its ``registry`` options are the ones the real pack
declares for the roles the loop reads (``evaluator.include_reference_solution``
and the tutor modes); ``tests/core/test_loop_options.py`` checks the real
manifest against the same parsers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import DomainAdapterRegistry
from harness.core.loop import LearningLoop
from harness.core.pack.canonical_json import document_hash
from harness.core.pack.importer import PackImporter
from harness.core.pack.model import PackRef
from harness.core.ports import (
    JsonObject,
    LLMRequest,
    LLMResponse,
    NetworkMode,
    ResourceLimits,
)
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import (
    FakeCommandExitCheck,
    FakeDomainAdapter,
    FakeFixtureProvider,
    FakeLabRuntime,
    FakeLLMProvider,
    FakeTerminalBridge,
    FakeTerminalTool,
    InMemoryEventStore,
    InMemoryPackSource,
)

PACK_ID = "loop-test"
PACK_VERSION = "0.1.0"
PACK_LOCATION = "memory://loop-test"
LEARNER_ID = "usr_learner1"
SKILL_ID = "loop.dns"
ACTIVITY_ID = "fix-resolver"
HARNESS_VERSION = "0.1.0-test"
IMAGE_DIGEST = "sha256:" + "a1" * 32
SOLUTION_SECRET = (
    "SECRET FIX: /etc/resolv.conf names a dead server; point it at 127.0.0.53 instead."
)

_LLM = {
    "provider": "fake",
    "model": "fake-model-1",
    "generation_parameters": {"temperature": 0, "max_tokens": 2000},
}


def _llm(prompt_id: str) -> dict[str, Any]:
    return {**_LLM, "prompt_id": prompt_id, "prompt_version": "1"}


SKILL: dict[str, Any] = {
    "id": SKILL_ID,
    "title": "Name resolution",
    "description": "Locate and repair the failing hop of a name lookup.",
    "prerequisites": [],
    "mastery_threshold": 0.8,
    "evidence_dimensions": ["observe", "diagnose", "repair"],
}

SOLUTION: dict[str, Any] = {
    "id": f"{ACTIVITY_ID}.solution",
    "activity_id": ACTIVITY_ID,
    "apply": [{"argv": ["sh", "-c", "echo fixed"], "timeout_seconds": 30}],
    "explanation": SOLUTION_SECRET,
}

ENVIRONMENT: dict[str, Any] = {
    "id": f"{ACTIVITY_ID}.env",
    "fixture": "fake.lab",
    "image": {"repository": "example.test/lab", "digest": IMAGE_DIGEST},
    "params": {"zone": "corp.internal"},
}

EVALUATOR: dict[str, Any] = {
    "id": "rubric-v1",
    "title": "Resolution diagnosis rubric",
    "semantic": {
        "dimensions": [
            {"id": "observe", "description": "Collected evidence before changing anything."},
            {"id": "diagnose", "description": "Localized the failing hop."},
            {"id": "repair", "description": "Fixed the cause, not the symptom."},
        ],
        "guidance": "A passing check is an observation, not proof of skill.",
    },
    "misconceptions": [
        {"id": "hosts-file-is-dns", "description": "Treats an /etc/hosts entry as fixing DNS."}
    ],
}

VISUALIZATION: dict[str, Any] = {
    "id": "resolution-flow",
    "version": "1",
    "title": "How a name becomes an address",
    "skills": [SKILL_ID],
    "diagram": {
        "type": "sequence",
        "actors": [
            {"id": "app", "label": "Application"},
            {"id": "resolver", "label": "Stub resolver"},
        ],
        "steps": [
            {
                "id": "ask",
                "from": "app",
                "to": "resolver",
                "label": "getaddrinfo",
                "explanation": "The application asks libc for an address.",
                "reality": {
                    "mechanism": "libc reads /etc/resolv.conf and sends a query.",
                    "observe": [
                        {"argv": ["cat", "/etc/resolv.conf"], "purpose": "See the server."}
                    ],
                },
            }
        ],
    },
}

REFERENCE: dict[str, Any] = {
    "id": "concept.resolver",
    "version": "1",
    "title": "Stub resolver",
    "summary": "The library that turns a name into an address.",
    "skills": [SKILL_ID],
    "sections": [{"kind": "what", "body": "A stub resolver forwards queries to a nameserver."}],
}

LAYOUT: dict[str, Any] = {"panes": [{"id": "terminal", "kind": "terminal"}]}


def _activity() -> dict[str, Any]:
    return {
        "id": ACTIVITY_ID,
        "title": "The service is up but nobody can find it",
        "activity_type": "diagnose",
        "skills": {"primary": [SKILL_ID]},
        "difficulty": 0.5,
        "origin": {"type": "authored"},
        "instructions": {"mission": "Make the name resolve again, then submit."},
        "hints": ["Ask DNS directly before editing anything."],
        "environment": ENVIRONMENT["id"],
        "tools": ["fake.terminal"],
        "checks": [{"check": "fake.exit", "params": {"argv": ["true"], "expected_exit_code": 0}}],
        "evaluator": EVALUATOR["id"],
        "reference_solution": {
            "path": "activities/fix-resolver.solution.json",
            "content_hash": document_hash(SOLUTION),
        },
        "remediation": {
            "references": [REFERENCE["id"]],
            "visualizations": [VISUALIZATION["id"]],
        },
    }


def _manifest(files: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pack_format": 1,
        "pack_id": PACK_ID,
        "pack_version": PACK_VERSION,
        "title": "Loop test pack",
        "description": "Hermetic pack for the learning-loop tests.",
        "domain_adapters": {"fake": ">=0.1.0,<0.2.0"},
        "registry": {
            "learner_model": {
                "implementation": "llm-learner-model@0.1.0",
                "llm": _llm("learner-model-update"),
                "output_schema": (
                    "https://morphloop.dev/contracts/schemas/llm/learner_model.update/1.json"
                ),
                "context_budget_tokens": 16000,
                "options": {"max_mastery_delta": 0.3},
            },
            "evaluator": {
                "implementation": "llm-evaluator@0.1.0",
                "llm": _llm("evaluator-judgment"),
                "output_schema": (
                    "https://morphloop.dev/contracts/schemas/llm/evaluator.judgment/1.json"
                ),
                "context_budget_tokens": 48000,
                "options": {"include_reference_solution": True},
            },
            "tutor": {
                "implementation": "llm-tutor@0.1.0",
                "llm": _llm("tutor-reply"),
                "output_schema": "https://morphloop.dev/contracts/schemas/llm/tutor.reply/1.json",
                "context_budget_tokens": 24000,
                "options": {
                    "modes": ["hint", "explain", "review"],
                    "default_mode_unfinished_attempt": "hint",
                    "default_mode_finished_attempt": "review",
                    "modes_allowed_unfinished_attempt": ["hint", "explain"],
                    "recent_events_limit": 30,
                },
            },
        },
        "files": dict(files),
    }


def pack_files() -> dict[str, bytes]:
    """The pack as ``{path: bytes}`` for :class:`InMemoryPackSource`."""
    documents: dict[str, tuple[str, Any]] = {
        "skills/resolution.json": ("skill", SKILL),
        "activities/fix-resolver.json": ("activity", _activity()),
        "activities/fix-resolver.solution.json": ("reference_solution", SOLUTION),
        "environments/fix-resolver.json": ("environment", ENVIRONMENT),
        "evaluators/rubric-v1.json": ("evaluator", EVALUATOR),
        "visualizations/resolution-flow.json": ("visualization", VISUALIZATION),
        "references/concept.resolver.json": ("reference", REFERENCE),
        "ux/layout.json": ("layout", LAYOUT),
    }
    prompts = {
        "prompts/learner-model-update.md": "learner-model-update",
        "prompts/evaluator-judgment.md": "evaluator-judgment",
        "prompts/tutor-reply.md": "tutor-reply",
    }
    index: dict[str, Any] = {path: {"kind": kind} for path, (kind, _) in documents.items()}
    for path, prompt_id in prompts.items():
        index[path] = {"kind": "prompt", "prompt_id": prompt_id, "prompt_version": "1"}
    files = {
        path: json.dumps(document, sort_keys=True).encode()
        for path, (_, document) in documents.items()
    }
    for path, prompt_id in prompts.items():
        files[path] = f"# {prompt_id}\n\nYou are the {prompt_id} role.\n".encode()
    files["manifest.json"] = json.dumps(_manifest(index), sort_keys=True).encode()
    return files


def fake_adapter() -> FakeDomainAdapter:
    """``fake@0.1.0`` with the fixture, check and terminal the test pack names."""
    limits = ResourceLimits(cpus=1, memory_bytes=1 << 28, pids=64, lifetime_seconds=600)
    network: NetworkMode = "none"
    return FakeDomainAdapter(
        adapter_id="fake",
        version="0.1.0",
        fixtures={"lab": FakeFixtureProvider(limits=limits, network=network)},
        checks={"exit": FakeCommandExitCheck(timeout_seconds=5, max_output_bytes=4096)},
        tools={"terminal": FakeTerminalTool()},
    )


class StepClock:
    """A clock that advances one second per call, so timestamps are stable."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._now
        self._now += timedelta(seconds=1)
        return value


def counting_ids() -> Callable[[str], str]:
    """Deterministic ids: ``evt_1``, ``att_2``, ... (one counter for all prefixes)."""
    counter = {"n": 0}

    def generate(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}_{counter['n']}"

    return generate


@dataclass(slots=True)
class LoopFixture:
    """Everything a loop test needs, already wired to fakes."""

    store: InMemoryEventStore
    loop: LearningLoop
    llm: RoleLLM | FakeLLMProvider
    labs: FakeLabRuntime
    terminals: FakeTerminalBridge
    adapters: DomainAdapterRegistry
    schemas: ContractSchemas
    pack: PackRef
    clock: StepClock = field(default_factory=StepClock)


def build_loop(*, llm: RoleLLM | FakeLLMProvider | None = None) -> LoopFixture:
    """Import the test pack into a fresh in-memory store and wire a loop over it."""
    store = InMemoryEventStore()
    schemas = ContractSchemas.load()
    adapters = DomainAdapterRegistry()
    adapters.register(fake_adapter())
    source = InMemoryPackSource({PACK_LOCATION: pack_files()})
    importer = PackImporter(
        source=source,
        store=store,
        schemas=schemas,
        adapters=adapters,
        algorithms=v01_algorithm_registry(),
    )
    result = importer.import_pack(PACK_LOCATION)
    provider: RoleLLM | FakeLLMProvider = llm if llm is not None else RoleLLM()
    labs = FakeLabRuntime()
    terminals = FakeTerminalBridge()
    clock = StepClock()
    loop = LearningLoop(
        store=store,
        schemas=schemas,
        adapters=adapters,
        algorithms=v01_algorithm_registry(),
        llm=provider,
        labs=labs,
        terminals=terminals,
        harness_version=HARNESS_VERSION,
        ids=counting_ids(),
        now=clock,
    )
    return LoopFixture(
        store=store,
        loop=loop,
        llm=provider,
        labs=labs,
        terminals=terminals,
        adapters=adapters,
        schemas=schemas,
        pack=result.ref,
        clock=clock,
    )


# --- scripted LLM outputs ---------------------------------------------------


def evaluator_output(*, success: bool = True, supporting: list[str] | None = None) -> JsonObject:
    """A schema-valid ``evaluator.judgment`` output."""
    return {
        "success": success,
        "rationale": "Queried the resolver before changing it, then fixed the configuration.",
        "evidence": [
            {
                "skill_id": SKILL_ID,
                "signal": "positive" if success else "negative",
                "strength": 0.8,
                "dimension": "diagnose",
                "rationale": "Narrowed the failure to the configured nameserver.",
                "supporting_event_ids": list(supporting or []),
            }
        ],
    }


def learner_model_output(*, evidence_ids: list[str], mastery: float = 0.45) -> JsonObject:
    """A schema-valid ``learner_model.update`` output."""
    return {
        "next": {"mastery_probability": mastery, "uncertainty": 0.4, "hint_dependency": 0.2},
        "evidence_ids": evidence_ids,
        "rationale": "One systematic diagnosis without hints.",
        "predicted_success_probability": 0.5,
    }


def tutor_output(*, text: str = "What does the resolver configuration say?") -> JsonObject:
    """A schema-valid ``tutor.reply`` output with no references."""
    return {"text": text, "references": []}


type Handler = Callable[[LLMRequest], JsonObject]


def _evaluator_handler(request: LLMRequest) -> JsonObject:
    context = json.loads(request.messages[-1].content)
    event_ids = [event["event_id"] for event in context["events"]]
    return evaluator_output(supporting=event_ids[:1])


def _learner_model_handler(request: LLMRequest) -> JsonObject:
    context = json.loads(request.messages[-1].content)
    return learner_model_output(evidence_ids=[item["evidence_id"] for item in context["evidence"]])


def _tutor_handler(request: LLMRequest) -> JsonObject:
    return tutor_output()


class RoleLLM:
    """An :class:`~harness.core.ports.LLMProvider` answering per role.

    Each handler sees the real request, so an answer can depend on what core
    actually sent (the evidence ids of the call, the events in the context).
    """

    def __init__(self, **handlers: Handler | JsonObject | Exception) -> None:
        self._handlers: dict[str, Handler | JsonObject | Exception] = {
            "evaluator": _evaluator_handler,
            "learner_model": _learner_model_handler,
            "tutor": _tutor_handler,
            **handlers,
        }
        self.requests: list[LLMRequest] = []

    def set(self, role: str, handler: Handler | JsonObject | Exception) -> None:
        self._handlers[role] = handler

    def context_of(self, role: str) -> dict[str, Any]:
        """The context object of the last call for ``role`` (tests assert on it)."""
        for request in reversed(self.requests):
            if request.role == role:
                contents: dict[str, Any] = json.loads(request.messages[-1].content)
                return contents
        raise AssertionError(f"no {role} request was made")

    def complete_structured(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        handler = self._handlers[request.role]
        if isinstance(handler, Exception):
            raise handler
        output = handler(request) if callable(handler) else handler
        return LLMResponse(output=output, provenance=request.llm)
