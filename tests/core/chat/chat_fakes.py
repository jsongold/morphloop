"""Shared test doubles for the chat resource (#63): a scripted tool-calling LLM
and a store holding one ws thread (``thread.created`` comes from the ws
resource, #57; a stand-in payload schema is added when it is not in contracts/)."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from harness.core.chat import AssistantConfig
from harness.core.contract_schemas import ContractSchemas
from harness.core.ports.events_v2 import EventV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import (
    LLMProvenance,
    LLMToolCall,
    LLMToolRequest,
    LLMToolResponse,
)
from harness.testing.contracts import CONTRACTS_DIR
from harness.testing.fakes_v2 import InMemoryEventStoreV2

SESSION = "ses_1"
WS = "ws_1"
THREAD = "thr_1"
USER = "usr_local"  # MORPHLOOP_USER_ID default
LLM = LLMProvenance(
    provider="fake",
    model="fake/model",
    prompt_id="chat-assistant",
    prompt_version="1",
    generation_parameters={},
)
CONFIG = AssistantConfig(llm=LLM, system_prompt="You are the assistant.", max_tool_rounds=2)


def text(content: str) -> LLMToolResponse:
    return LLMToolResponse(content=content, tool_calls=(), provenance=LLM)


def call(name: str, arguments: JsonObject | None = None) -> LLMToolResponse:
    tool_call = LLMToolCall(id=f"c_{name}", name=name, arguments=arguments or {})
    return LLMToolResponse(content=None, tool_calls=(tool_call,), provenance=LLM)


class FakeToolProvider:
    """Returns scripted responses (or raises scripted exceptions) in order."""

    def __init__(self, script: Sequence[LLMToolResponse | Exception]) -> None:
        self.script = list(script)
        self.requests: list[LLMToolRequest] = []

    def complete_with_tools(self, request: LLMToolRequest) -> LLMToolResponse:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def store_with_thread(
    tmp_path: Path, *, target: JsonObject | None = None, labels: Sequence[str] = ()
) -> InMemoryEventStoreV2:
    contracts = tmp_path / "contracts"
    shutil.copytree(CONTRACTS_DIR, contracts)
    schema = contracts / "schemas/events/payloads/thread.created/1.json"
    if not schema.exists():
        schema.parent.mkdir(parents=True)
        schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": "https://morphloop.dev/contracts/schemas/events/payloads/thread.created/1.json",
                    "x-envelope": 2,
                    "x-actors": ["learner", "assistant", "system"],
                    "type": "object",
                }
            ),
            encoding="utf-8",
        )
    store = InMemoryEventStoreV2(ContractSchemas(contracts))
    payload: dict[str, object] = {"thread_id": THREAD, "labels": list(labels)}
    if target is not None:
        payload["target"] = target
    with store.transaction() as tx:
        tx.append(
            EventV2(
                id=str(uuid.uuid4()),
                type="thread.created",
                actor="learner",
                user_id=USER,
                session_id=SESSION,
                ws_id=WS,
                payload=payload,  # type: ignore[arg-type]
            )
        )
    return store
