"use client";

import type { TocChapter, TocItem } from "@/lib/layout";

interface Props {
  chapters: TocChapter[];
  /** Resolves an item to its display title (pack content titles). */
  titleOf: (item: TocItem) => string;
  isCurrent: (item: TocItem) => boolean;
  onSelect: (item: TocItem) => void;
}

/**
 * Book-like table of contents declared by the pack (layout `toc`). Navigation
 * only: every item is always selectable, in any order (not a linear course).
 * Starts collapsed at narrow widths.
 */
export function TocPane({ chapters, titleOf, isCurrent, onSelect }: Props) {
  const wide = typeof window === "undefined" || window.matchMedia("(min-width: 900px)").matches;
  return (
    <details className="toc" open={wide}>
      <summary>Contents</summary>
      <ol>
        {chapters.map((c, i) => (
          <li key={i}>
            <strong>{c.title}</strong>
            <ul>
              {c.items.map((item) => (
                <li key={`${item.kind}/${item.id}`}>
                  <button
                    className="link"
                    aria-current={isCurrent(item) ? "true" : undefined}
                    onClick={() => onSelect(item)}
                  >
                    {titleOf(item)}
                  </button>{" "}
                  <span className="muted">{item.kind}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </details>
  );
}
