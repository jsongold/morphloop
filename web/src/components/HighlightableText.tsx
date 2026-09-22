"use client";

import type { HighlightEvent } from "@/lib/types";

interface Props {
  text: string;
  contentId: string;
  contentVersion: string;
  anchor?: string | null;
  highlights: HighlightEvent[];
  as?: "div" | "p" | "span";
  className?: string;
}

/**
 * Plain text region that can be selected and highlighted. Stored highlights
 * for the same (content, version, anchor) are rendered as <mark>, so a reload
 * visibly restores them (AC-D2). textContent equals `text`, keeping offsets
 * stable.
 */
export function HighlightableText({
  text,
  contentId,
  contentVersion,
  anchor = null,
  highlights,
  as: Tag = "div",
  className,
}: Props) {
  const ranges = highlights
    .map((h) => h.payload)
    .filter(
      (p) =>
        p.content_id === contentId &&
        p.content_version === contentVersion &&
        (p.semantic_anchor ?? null) === anchor &&
        p.end_offset <= text.length &&
        text.slice(p.start_offset, p.end_offset) === p.selected_text,
    )
    .sort((a, b) => a.start_offset - b.start_offset);

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const r of ranges) {
    if (r.start_offset < cursor) continue; // overlapping: keep the earlier one
    if (r.start_offset > cursor) parts.push(text.slice(cursor, r.start_offset));
    parts.push(
      <mark key={r.highlight_id} title="Saved highlight">
        {text.slice(r.start_offset, r.end_offset)}
      </mark>,
    );
    cursor = r.end_offset;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));

  return (
    <Tag
      className={className}
      style={{ whiteSpace: "pre-wrap" }}
      data-hl-content-id={contentId}
      data-hl-content-version={contentVersion}
      data-hl-anchor={anchor ?? undefined}
    >
      {parts}
    </Tag>
  );
}
