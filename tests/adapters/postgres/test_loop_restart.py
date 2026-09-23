"""An api restart loses nothing the loop needs (Postgres; see conftest.py).

"Restart" is a fresh :class:`LearningLoop` over the same store: no in-process
state survives, only the event log and its projections. A failed evaluation
(``evaluation.failed``) and a lab's ``runtime_ref`` (``lab.started`` v2) must
both be recoverable from there.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

from loop_harness import (  # noqa: E402
    ACTIVITY_ID,
    HARNESS_VERSION,
    PACK_LOCATION,
    RoleLLM,
    fake_adapter,
    pack_files,
)

from harness.adapters.postgres import PostgresEventStore  # noqa: E402
from harness.core.contract_schemas import ContractSchemas  # noqa: E402
from harness.core.domain_adapter import DomainAdapterRegistry  # noqa: E402
from harness.core.loop import LearningLoop, LLMFailedError, StateConflictError  # noqa: E402
from harness.core.pack.importer import PackImporter  # noqa: E402
from harness.core.pack.model import PackRef  # noqa: E402
from harness.core.ports import LLMError, TerminalSize  # noqa: E402
from harness.core.registry.builtin import v01_algorithm_registry  # noqa: E402
from harness.testing.fakes import (  # noqa: E402
    FakeLabRuntime,
    FakeTerminalBridge,
    InMemoryPackSource,
)


class Stack:
    """The process-independent parts; :meth:`loop` is one api process."""

    def __init__(self, store: PostgresEventStore) -> None:
        self.store = store
        self.schemas = ContractSchemas.load()
        self.adapters = DomainAdapterRegistry()
        self.adapters.register(fake_adapter())
        self.labs = FakeLabRuntime()  # the containers outlive the api process
        self.terminals = FakeTerminalBridge()
        self.llm = RoleLLM()
        self.pack: PackRef = (
            PackImporter(
                source=InMemoryPackSource({PACK_LOCATION: pack_files()}),
                store=store,
                schemas=self.schemas,
                adapters=self.adapters,
                algorithms=v01_algorithm_registry(),
            )
            .import_pack(PACK_LOCATION)
            .ref
        )

    def loop(self) -> LearningLoop:
        return LearningLoop(
            store=self.store,
            schemas=self.schemas,
            adapters=self.adapters,
            algorithms=v01_algorithm_registry(),
            llm=self.llm,
            labs=self.labs,
            terminals=self.terminals,
            harness_version=HARNESS_VERSION,
        )


def _key() -> str:
    return f"test:{uuid.uuid4().hex}"


def _start(stack: Stack, loop: LearningLoop) -> tuple[str, str, str]:
    session = loop.start_session(
        learner_id=f"usr_{uuid.uuid4().hex[:16]}", pack=stack.pack, idempotency_key=_key()
    )
    attempt = loop.start_attempt(
        session_id=session.session.session_id,
        activity_definition_id=ACTIVITY_ID,
        idempotency_key=_key(),
    )
    assert attempt.lab is not None
    return session.session.session_id, attempt.attempt_id, attempt.lab.lab_instance_id


def test_a_failed_evaluation_is_resubmittable_after_a_restart(store: PostgresEventStore) -> None:
    stack = Stack(store)
    before = stack.loop()
    session_id, attempt_id, _ = _start(stack, before)
    stack.llm.set("evaluator", LLMError("the model is unreachable"))
    before.submit_attempt(attempt_id=attempt_id, idempotency_key=_key())
    with pytest.raises(LLMFailedError):
        before.evaluate_attempt(attempt_id)

    after = stack.loop()
    state = after.attempt_state(attempt_id)
    assert state.status == "active"
    assert state.last_submission_error is not None
    assert state.last_submission_error["code"] == "llm-failed"
    failed = [e for e in store.read_session(session_id) if e.event_type == "evaluation.failed"]
    assert len(failed) == 1 and failed[0].attempt_id == attempt_id

    stack.llm = RoleLLM()
    after = stack.loop()
    resubmitted = after.submit_attempt(attempt_id=attempt_id, idempotency_key=_key())
    assert resubmitted.status == "evaluating"
    assert resubmitted.last_submission_error is None
    completed = after.evaluate_attempt(attempt_id)
    assert completed.status == "completed"
    with pytest.raises(StateConflictError):
        after.submit_attempt(attempt_id=attempt_id, idempotency_key=_key())


@pytest.mark.asyncio
async def test_a_terminal_reattaches_to_the_lab_after_a_restart(
    store: PostgresEventStore,
) -> None:
    stack = Stack(store)
    _, _, lab_instance_id = _start(stack, stack.loop())

    after = stack.loop()
    connection = await after.open_terminal(
        lab_instance_id=lab_instance_id, size=TerminalSize(cols=80, rows=24)
    )

    assert stack.terminals.sessions[-1].request.runtime_ref == f"fake-{lab_instance_id}"
    assert after.lab_state(lab_instance_id).terminal_id == connection.terminal_id
