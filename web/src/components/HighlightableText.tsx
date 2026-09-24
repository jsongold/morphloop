"use client";

import type { ContentHighlightedPayload, HighlightEvent } from "@/lib/types";

interface Props {
  text: string;
  /** Pack content id, or the chat message id when `sourceKind === "chat_message"`. */
  contentId: string;
  /** Unused for chat_message sources. */
  contentVersion: string;
  anchor?: string | null;
  /** "chat_message" renders/highlights a chat message; default "pack". */
  sourceKind?: "pack" | "chat_message";
  highlights: HighlightEvent[];
  as?: "div" | "p" | "span";
  className?: string;
}

/**
 * Plain text region that can be selected and highlighted. Stored highlights
 * for the same source are rendered as <mark>, so a reload visibly restores
 * them (AC-D2). textContent equals `text`, keeping offsets stable.
 */
export function HighlightableText({
  text,
  contentId,
  contentVersion,
  anchor = null,
  sourceKind = "pack",
  highlights,
  as: Tag = "div",
  className,
}: Props) {
  const matches =
    sourceKind === "chat_message"
      ? (p: ContentHighlightedPayload) =>
          p.source.kind === "chat_message" &&
          p.source.message_id === contentId &&
          p.start_offset !== null &&
          p.end_offset !== null &&
          p.end_offset <= text.length &&
          text.slice(p.start_offset, p.end_offset) === p.selected_text
      : (p: ContentHighlightedPayload) =>
          p.source.kind === "pack" &&
          p.source.content_id === contentId &&
          p.source.content_version === contentVersion &&
          (p.semantic_anchor ?? null) === anchor &&
          p.start_offset !== null &&
          p.end_offset !== null &&
          p.end_offset <= text.length &&
          text.slice(p.start_offset, p.end_offset) === p.selected_text;

  const ranges = highlights
    .map((h) => h.payload)
    .filter(matches)
    .sort((a, b) => (a.start_offset ?? 0) - (b.start_offset ?? 0));

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const r of ranges) {
    const start = r.start_offset ?? 0;
    const end = r.end_offset ?? 0;
    if (start < cursor) continue; // overlapping: keep the earlier one
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={r.highlight_id} title="Saved highlight">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));

  return (
    <Tag
      className={className}
      style={{ whiteSpace: "pre-wrap" }}
      data-hl-content-id={contentId}
      data-hl-content-version={contentVersion}
      data-hl-anchor={anchor ?? undefined}
      data-hl-kind={sourceKind === "chat_message" ? "chat_message" : undefined}
    >
      {parts}
    </Tag>
  );
}
