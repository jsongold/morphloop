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

// A learner can type the next message while a send is in flight. Only clear the
// composer when it still holds the text that was just submitted; otherwise the
// follow-up typed during the wait would be erased.
export const shouldClearDraft = (current: string, submitted: string) => current.trim() === submitted.trim();

// Reuse the idempotency key of an in-flight action until it succeeds: a lost
// response must not turn the retry into a fresh request (the server replays the
// stored result for the same key instead).
export function reuseKey(keys: Map<string, string>, id: string, newKey: () => string): string {
  const existing = keys.get(id) ?? newKey();
  keys.set(id, existing);
  return existing;
}

// The server response is authoritative, but a slow GET can resolve after a
// local mutation. Keep the primary list's order and append only the extra items
// (those the response did not include) so nothing saved mid-load is dropped.
export function mergeById<T>(primary: T[], extra: T[], id: (item: T) => string): T[] {
  const seen = new Set(primary.map(id));
  return [...primary, ...extra.filter((item) => !seen.has(id(item)))];
}
