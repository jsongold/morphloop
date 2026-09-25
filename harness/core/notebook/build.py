"""Build a workspace from a session and related learning content."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from harness.core.drill.service import DrillService
from harness.core.drill.store import generated_items, pack_items
from harness.core.labels import check_labels
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import EventIdConflictError, EventTransactionV2
from harness.core.ports.generated_documents import GeneratedDocumentStore
from harness.core.session.model import TopicNotFoundError, find_topic
from harness.core.session.service import get_session
from harness.core.textbook.service import Textbook
from harness.core.ws.service import create_ws


def build_workspace(
    tx: EventTransactionV2,
    *,
    pack: PackV2,
    generated: GeneratedDocumentStore,
    event_id: str,
    user_id: str,
    session_id: str,
    topic: str | None = None,
    labels: Sequence[str] = (),
) -> dict[str, Any]:
    """Create one ws and return its textbook and learner-safe drill selection."""
    session = get_session(tx, session_id, user_id=user_id)
    if session is None:
        raise LookupError(f"no session {session_id!r}")
    if (topic is None) == (not labels):
        raise ValueError("provide exactly one of topic or labels")
    selected_labels = [f"topic:{topic}"] if topic is not None else list(labels)
    existing = tx.get(event_id)
    if existing is not None and (
        existing.type != "ws.created"
        or existing.user_id != user_id
        or existing.session_id != session_id
        or existing.payload.get("labels") != [*selected_labels, "origin:learner"]
    ):
        raise EventIdConflictError(existing)
    if existing is None:
        check_labels(selected_labels, vocabulary=pack.labels, topic_ids=pack.topic_ids)
        if topic is not None:
            tree = session.get("tree")
            if not isinstance(tree, Mapping):
                raise LookupError(f"no topic {topic!r} in session {session_id!r}")
            try:
                find_topic((tree,), topic)
            except TopicNotFoundError as exc:
                raise LookupError(f"no topic {topic!r} in session {session_id!r}") from exc
    textbook = Textbook(pack, generated)
    if topic is not None:
        documents = textbook.reading_list(topic)
    else:
        wanted = set(labels)
        documents = []
        for doc in textbook.list_docs():
            doc_labels = doc.get("labels")
            if isinstance(doc_labels, list) and wanted <= set(str(label) for label in doc_labels):
                documents.append(doc)
    drills = DrillService([*pack_items(pack), *generated_items(generated.list("drill"))])
    workspace = create_ws(
        tx, event_id=event_id, user_id=user_id, session_id=session_id, labels=selected_labels
    )
    return {
        "workspace": workspace.to_dict(),
        "documents": documents,
        "drills": [item.for_learner() for item in drills.list_items(selected_labels)],
    }
