"use client";

// Owned by the textbook pane PR; replace this directory. Keep both exports
// (src/v2/panes/types.ts): TextbookToc (left slot) and TextbookReader (main
// slot). Blocks must carry data-doc-id / data-block-id and render
// `::artifact{type ref}` through <ArtifactSlot/> from ../artifact.

export function TextbookToc() {
  return (
    <details className="toc" open>
      <summary>Contents</summary>
      <p className="muted">textbook toc: not implemented</p>
    </details>
  );
}

export function TextbookReader() {
  return <p className="muted">textbook reader: not implemented</p>;
}
