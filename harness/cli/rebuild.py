"""``rebuild``: drop the learning-loop projections and replay the event log (AC-F6).

The rebuild itself belongs to core:
:func:`harness.core.loop.rebuild_projections` clears
:data:`~harness.core.loop.LOOP_PROJECTIONS` and replays every stored event
through the same appliers the append path uses, in ``position`` order and in
one transaction (ADR-0008). It replays the update results already recorded on
the events; no LLM is called (ADR-0013). This command only wires a store to it
and reports what came back.

The pack projection is not touched: it is written by the Importer from the
pack files, not from the event log (ADR-0015), so ``import`` rebuilds that one.
"""

from __future__ import annotations

from harness.core.artifact_lab.lab import ArtifactView as _ArtifactView  # noqa: F401
from harness.core.chat.view import ChatMessagesView as _ChatMessagesView  # noqa: F401
from harness.core.chat.view import ChatThreadView as _ChatThreadView  # noqa: F401
from harness.core.drill.store import DrillAnswersView as _DrillAnswersView  # noqa: F401
from harness.core.highlight.view import HighlightView as _HighlightView  # noqa: F401
from harness.core.loop import LOOP_PROJECTIONS, rebuild_projections
from harness.core.memo.entries import MemoEntries as _MemoEntries  # noqa: F401
from harness.core.ports import EventStore
from harness.core.ports.events_v2 import EventStoreV2
from harness.core.session.model import SessionView as _SessionView  # noqa: F401
from harness.core.view import registered_views
from harness.core.ws.view import WsView as _WsView  # noqa: F401


def rebuild(store: EventStore, store_v2: EventStoreV2 | None = None) -> int:
    """Rebuild v0.1 projections and, when supplied, every registered v0.2 view."""
    replayed = rebuild_projections(store)
    if store_v2 is not None:
        events = store_v2.read()
        with store_v2.transaction() as tx:
            for view in registered_views().values():
                view.rebuild(events, tx)
    return replayed


def format_result(replayed: int) -> str:
    return f"replayed      {replayed} event(s)\nprojections   {', '.join(sorted(LOOP_PROJECTIONS))}"
