// Maps a DOM selection to a highlight target: which source (pack content,
// chat message), which in-document anchor, and character offsets into that
// region's text. Terminal selections are built separately (see TerminalPane).
//
// A highlightable region is an element with `data-hl-content-id`,
// `data-hl-content-version`, optional `data-hl-anchor` and optional
// `data-hl-kind` ("chat_message"; default "pack"). Offsets are measured over
// the region's `textContent`, which is exactly the plain text the region
// renders (see HighlightableText).

import type { HighlightSource } from "./types";

export interface SelectionTarget {
  source: HighlightSource;
  anchor: string | null;
  text: string;
  /** UTF-16 offsets into the source text; null for terminal targets. */
  start: number | null;
  end: number | null;
  contextBefore: string;
  contextAfter: string;
}

const CONTEXT_CHARS = 80;

function regionOf(node: Node | null): HTMLElement | null {
  const el = node instanceof HTMLElement ? node : node?.parentElement ?? null;
  return el?.closest<HTMLElement>("[data-hl-content-id]") ?? null;
}

function offsetWithin(region: HTMLElement, node: Node, offset: number): number {
  const r = document.createRange();
  r.selectNodeContents(region);
  r.setEnd(node, offset);
  return r.toString().length;
}

export function readSelection(): SelectionTarget | null {
  const sel = typeof window === "undefined" ? null : window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const region = regionOf(range.startContainer);
  if (!region || region !== regionOf(range.endContainer)) return null;
  const full = region.textContent ?? "";
  const start = offsetWithin(region, range.startContainer, range.startOffset);
  const end = offsetWithin(region, range.endContainer, range.endOffset);
  if (end <= start) return null;
  const text = full.slice(start, end);
  if (text.trim().length === 0) return null;
  const kind = region.dataset.hlKind === "chat_message" ? "chat_message" : "pack";
  const contentId = region.dataset.hlContentId ?? "";
  const contentVersion = region.dataset.hlContentVersion ?? "";
  const source: HighlightSource =
    kind === "chat_message"
      ? { kind: "chat_message", message_id: contentId }
      : { kind: "pack", content_id: contentId, content_version: contentVersion };
  return {
    source,
    anchor: region.dataset.hlAnchor ?? null,
    text,
    start,
    end,
    contextBefore: full.slice(Math.max(0, start - CONTEXT_CHARS), start),
    contextAfter: full.slice(end, end + CONTEXT_CHARS),
  };
}
