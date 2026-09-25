"""memo resource: an append-only learning log, a child of ws (#34, #59)."""

from __future__ import annotations

from harness.core.memo.entries import MemoEntries, append_memo_entry, entry_id_for

__all__ = ["MemoEntries", "append_memo_entry", "entry_id_for"]
