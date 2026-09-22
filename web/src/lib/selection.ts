// Maps a DOM selection to a highlight target: which content document, which
// in-document anchor, and character offsets into that region's text.
//
// A highlightable region is an element with `data-hl-content-id`,
// `data-hl-content-version` and optional `data-hl-anchor`. Offsets are
// measured over the region's `textContent`, which is exactly the plain text
// the region renders (see HighlightableText).

export interface SelectionTarget {
  contentId: string;
  contentVersion: string;
  anchor: string | null;
  text: string;
  start: number;
  end: number;
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
  return {
    contentId: region.dataset.hlContentId ?? "",
    contentVersion: region.dataset.hlContentVersion ?? "",
    anchor: region.dataset.hlAnchor ?? null,
    text,
    start,
    end,
    contextBefore: full.slice(Math.max(0, start - CONTEXT_CHARS), start),
    contextAfter: full.slice(end, end + CONTEXT_CHARS),
  };
}
