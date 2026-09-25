"""Chat tool registry: notebook functions the assistant may call (#34, #63).

A tool is a :class:`ChatTool` subclass; defining it (importing its module)
registers it, so a resource or the notebook adds tools without editing a
central list. ``writes`` is mandatory: a write tool is offered to the model
only when the learner explicitly commanded a write (``allow_writes``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from harness.core.ports.events_v2 import EventStoreV2
from harness.core.ports.json_types import JsonObject
from harness.core.ports.llm import LLMTool

_REGISTRY: dict[str, type[ChatTool]] = {}


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext:
    """What a tool may see: the thread it runs for, plus the store to read or write."""

    store: EventStoreV2
    user_id: str
    session_id: str | None
    ws_id: str
    thread_id: str
    target: JsonObject | None
    labels: tuple[str, ...]


class ChatTool:
    """Base class; subclasses set every ClassVar and implement :meth:`run`."""

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[JsonObject]
    writes: ClassVar[bool]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        missing = [
            a for a in ("name", "description", "parameters", "writes") if not hasattr(cls, a)
        ]
        if missing:
            raise TypeError(f"ChatTool {cls.__qualname__} must declare {missing}")
        existing = _REGISTRY.get(cls.name)
        if existing is not None and existing.__qualname__ != cls.__qualname__:
            raise TypeError(f"chat tool {cls.name!r} is already registered by {existing!r}")
        _REGISTRY[cls.name] = cls

    @classmethod
    def spec(cls) -> LLMTool:
        return LLMTool(name=cls.name, description=cls.description, parameters=cls.parameters)

    @classmethod
    def run(cls, arguments: JsonObject, ctx: ToolContext) -> JsonObject:
        """Run with arguments already validated against ``parameters``."""
        raise NotImplementedError


def offered_tools(*, allow_writes: bool) -> Mapping[str, type[ChatTool]]:
    """Registered tools the model may call: read tools always, write tools on command."""
    return {n: t for n, t in _REGISTRY.items() if allow_writes or not t.writes}


class ThreadTarget(ChatTool):
    """Read-only: the current thread's target and labels."""

    name = "thread_target"
    description = "Return the target (what this thread is about) and labels of the current thread."
    parameters: ClassVar[JsonObject] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    writes = False

    @classmethod
    def run(cls, arguments: JsonObject, ctx: ToolContext) -> JsonObject:
        return {"target": ctx.target, "labels": list(ctx.labels)}
