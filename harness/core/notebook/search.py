"""Substring search across textbook, memo, and drill read models."""

from __future__ import annotations

from harness.core.drill.service import DrillService
from harness.core.drill.store import generated_items, pack_items
from harness.core.memo.entries import MemoEntries
from harness.core.pack.v2 import PackV2
from harness.core.ports.events_v2 import EventTransactionV2
from harness.core.ports.generated_documents import GeneratedDocumentStore
from harness.core.ports.json_types import PlainJson
from harness.core.textbook.service import Textbook
from harness.core.ws.view import WsView


def search(
    query: str,
    *,
    pack: PackV2,
    generated: GeneratedDocumentStore,
    tx: EventTransactionV2,
    user_id: str,
    session_id: str | None = None,
) -> list[dict[str, PlainJson]]:
    """Search learner-visible text, with case-insensitive substring matching."""
    needle = query.strip().casefold()
    if not needle:
        return []
    results: list[dict[str, PlainJson]] = []
    textbook = Textbook(pack, generated)
    for doc in textbook.all_docs():
        doc_id = str(doc["id"])
        title = str(doc["title"])
        if needle in title.casefold():
            results.append({"kind": "textbook_doc", "id": doc_id, "text": title})
        blocks = doc.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = block.get("plaintext")
            if isinstance(text, str) and needle in text.casefold():
                results.append(
                    {
                        "kind": "textbook_block",
                        "id": doc_id,
                        "block_id": str(block.get("id", "")),
                        "text": text,
                    }
                )
    drills = DrillService([*pack_items(pack), *generated_items(generated.list("drill"))])
    results.extend(
        {"kind": "drill_item", "id": item.id, "text": item.question}
        for item in drills.list_items()
        if needle in item.question.casefold()
    )
    # ponytail: ws view has only a ws_id key; add a user/session index if v0.4 scale needs it.
    for ws_id, ws in WsView.list(tx):
        if ws.get("user_id") != user_id or (
            session_id is not None and ws.get("session_id") != session_id
        ):
            continue
        for entry in MemoEntries.list_for_ws(tx, ws_id):
            body = entry.get("body")
            if isinstance(body, str) and needle in body.casefold():
                results.append({"kind": "memo_entry", "id": str(entry["entry_id"]), "text": body})
    return results
