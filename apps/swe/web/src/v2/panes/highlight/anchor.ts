export type Anchor = {
  doc_id: string;
  block_id: string;
  selector: [
    { type: "TextQuoteSelector"; exact: string; prefix: string; suffix: string },
    { type: "TextPositionSelector"; start: number; end: number },
  ];
};

export const chars = (text: string) => Array.from(text);

export function anchorFor(docId: string, blockId: string, plaintext: string, start: number, end: number): Anchor | null {
  const text = chars(plaintext);
  const exact = text.slice(start, end).join("");
  if (start < 0 || end > text.length || start >= end || !exact.trim()) return null;
  return {
    doc_id: docId,
    block_id: blockId,
    selector: [
      { type: "TextQuoteSelector", exact, prefix: text.slice(Math.max(0, start - 40), start).join(""), suffix: text.slice(end, end + 40).join("") },
      { type: "TextPositionSelector", start, end },
    ],
  };
}
