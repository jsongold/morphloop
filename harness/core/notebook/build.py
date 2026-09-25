"""Build a workspace from a session and related learning content."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from harness.core.drill.service import DrillService
from harness.core.drill.store import generated_items, pack_items
from harness.core.labels import check_labels
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import (
    EventIdConflictError,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.generated_documents import GeneratedDocumentStore
from harness.core.session.model import TopicNotFoundError, find_topic
from harness.core.session.service import PackMismatchError, get_session
from harness.core.textbook.service import Textbook
from harness.core.ws.service import create_ws

BUILT = "notebook.built"


def _selection_id(event_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BUILT}:{event_id}"))


def _ws_event_conflicts(
    existing: StoredEventV2,
    *,
    user_id: str,
    session_id: str,
    labels: Sequence[str],
) -> bool:
    """Whether ``existing`` is not the ``ws.created`` this build would have made."""
    return (
        existing.type != "ws.created"
        or existing.user_id != user_id
        or existing.session_id != session_id
        or existing.payload.get("labels") != list(labels)
    )


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
    if (
        session["pack_id"] != pack.pack_id
        or session["pack_version"] != pack.pack_version
        or session["pack_content_hash"] != pack.pack_hash
    ):
        raise PackMismatchError(
            f"session {session_id!r} pins pack {session['pack_id']!r} "
            f"version {session['pack_version']!r} with hash {session['pack_content_hash']!r}; "
            f"loaded pack is {pack.pack_id!r} version {pack.pack_version!r} "
            f"with hash {pack.pack_hash!r}"
        )
    if (topic is None) == (not labels):
        raise ValueError("provide exactly one of topic or labels")
    selected_labels = [f"topic:{topic}"] if topic is not None else list(labels)
    selector = "topic" if topic is not None else "labels"
    expected_labels = [*selected_labels, "origin:learner"]
    existing = tx.get(event_id)
    if existing is not None and _ws_event_conflicts(
        existing, user_id=user_id, session_id=session_id, labels=expected_labels
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
    drills = DrillService([*pack_items(pack), *generated_items(generated.list("drill"))])
    selection = tx.get(_selection_id(event_id))
    if selection is None:
        if existing is not None:
            # The same Idempotency-Key created this ws through another
            # operation (e.g. POST /v2/ws) with no paired notebook.built
            # selection: the key is reused, not a replay of this request.
            raise EventIdConflictError(existing)
        if topic is not None:
            documents = textbook.reading_list(topic)
        else:
            wanted = set(labels)
            documents = []
            for doc in textbook.list_docs():
                doc_labels = doc.get("labels")
                if isinstance(doc_labels, list) and wanted <= set(
                    str(label) for label in doc_labels
                ):
                    documents.append(doc)
        items = [item.for_learner() for item in drills.list_items(selected_labels)]
    else:
        if existing is None:
            # A concurrent build of the same body can commit both events while
            # this request loads content. Under READ COMMITTED this re-read
            # sees the now-committed workspace event instead of 409ing a replay.
            existing = tx.get(event_id)
        if existing is None or _ws_event_conflicts(
            existing, user_id=user_id, session_id=session_id, labels=expected_labels
        ):
            raise EventIdConflictError(selection)
        if (
            selection.type != BUILT
            or selection.user_id != user_id
            or selection.session_id != session_id
            or selection.ws_id != existing.ws_id
        ):
            raise EventIdConflictError(selection)
        if selection.payload.get("selector") != selector:
            raise EventIdConflictError(existing)
        doc_ids = selection.payload["doc_ids"]
        item_ids = selection.payload["drill_item_ids"]
        if not isinstance(doc_ids, list) or not isinstance(item_ids, list):
            raise RuntimeError("invalid notebook selection payload")
        documents = []
        for doc_id in doc_ids:
            doc = textbook.doc(str(doc_id))
            documents.append({key: doc[key] for key in ("id", "title", "labels")})
        items = [drills.get_item(str(item_id)).for_learner() for item_id in item_ids]
    workspace = create_ws(
        tx, event_id=event_id, user_id=user_id, session_id=session_id, labels=selected_labels
    )
    if selection is None:
        tx.append(
            EventV2(
                id=_selection_id(event_id),
                type=BUILT,
                actor="learner",
                user_id=user_id,
                session_id=session_id,
                ws_id=workspace.ws_id,
                payload={
                    "selector": selector,
                    "doc_ids": [str(doc["id"]) for doc in documents],
                    "drill_item_ids": [str(item["id"]) for item in items],
                },
            )
        )
    return {
        "workspace": workspace.to_dict(),
        "documents": documents,
        "drills": items,
    }
