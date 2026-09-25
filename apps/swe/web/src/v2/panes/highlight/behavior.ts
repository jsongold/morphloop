import type { Anchor } from "./anchor";

export function threadTarget(highlightId: string, anchor: Anchor) {
  return { kind: "textbook_block", doc_id: anchor.doc_id, block_id: anchor.block_id, highlight_id: highlightId, selector: anchor.selector };
}

export type PendingHighlight = { fingerprint: string; key: string };

export function highlightKey(pending: PendingHighlight | null, wsId: string, anchor: Anchor, newKey: () => string): PendingHighlight {
  const fingerprint = JSON.stringify([wsId, anchor]);
  return pending?.fingerprint === fingerprint ? pending : { fingerprint, key: newKey() };
}

export const popupTop = (anchorY: number, viewportHeight: number, popupHeight: number) =>
  Math.max(8, Math.min(anchorY, viewportHeight - popupHeight - 8));

export const nearBottom = (scrollTop: number, clientHeight: number, scrollHeight: number) =>
  scrollHeight - scrollTop - clientHeight <= 32;

// A draft belongs to the thread it was typed in; switching threads must not carry it along.
export const keepsDraft = (openThreadId: string | null, nextThreadId: string) => openThreadId === nextThreadId;
