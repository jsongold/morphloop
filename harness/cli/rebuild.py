"""``rebuild``: replay the event log into every registered v0.2 view (ADR-0008).

Each view rebuilds itself from the stored events (:meth:`View.rebuild`), in
``position`` order and in one transaction. The update results already recorded
on the events are replayed; no LLM is called (ADR-0013).
"""

from __future__ import annotations

from harness.api.v2.routes import discover_routers
from harness.core.ports.events_v2 import EventStoreV2
from harness.core.view import registered_views


def rebuild(store_v2: EventStoreV2) -> int:
    """Rebuild every registered v0.2 view; returns how many events were replayed."""
    # Importing the /v2 route modules registers every SDK view, exactly as
    # the API does. Views an app registers are rebuilt by that app.
    list(discover_routers())
    events = store_v2.read()
    with store_v2.transaction() as tx:
        for view in registered_views().values():
            view.rebuild(events, tx)
    return len(events)


def format_result(replayed: int) -> str:
    views = ", ".join(sorted(registered_views()))
    return f"replayed      {replayed} event(s)\nviews         {views}"
