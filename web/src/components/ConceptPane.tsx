"use client";

import { HighlightableText } from "./HighlightableText";
import type {
  ContentDocument,
  ContentSummary,
  HighlightEvent,
  ReferenceDocument,
} from "@/lib/types";

interface Props {
  open: ContentDocument | null;
  catalog: ContentSummary[];
  highlights: HighlightEvent[];
  onOpen: (summary: ContentSummary) => void;
  onAskAboutHighlight: (h: HighlightEvent) => void;
}

/** Side pane: the opened pack content, the content catalog and saved highlights (AC-D1). */
export function ConceptPane({ open, catalog, highlights, onOpen, onAskAboutHighlight }: Props) {
  return (
    <div className="concept-pane">
      {open ? <OpenDocument doc={open} highlights={highlights} /> : (
        <p className="muted">Select text and choose “Open concept”, or pick an item below.</p>
      )}

      <details open={!open}>
        <summary>Content</summary>
        <ul>
          {catalog
            .filter((c) => c.kind !== "visualization")
            .map((c) => (
              <li key={`${c.kind}/${c.definition_id}`}>
                <button className="link" onClick={() => onOpen(c)}>
                  {c.title}
                </button>{" "}
                <span className="muted">{c.kind}</span>
              </li>
            ))}
        </ul>
      </details>

      <details open>
        <summary>Highlights ({highlights.length})</summary>
        <ul className="highlights">
          {highlights.map((h) => (
            <li key={h.payload.highlight_id}>
              <q>{h.payload.selected_text}</q>{" "}
              <button className="link" onClick={() => onAskAboutHighlight(h)}>
                Ask AI
              </button>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function OpenDocument({ doc, highlights }: { doc: ContentDocument; highlights: HighlightEvent[] }) {
  if (doc.kind === "reference") {
    const ref = doc.document as unknown as ReferenceDocument;
    return (
      <article>
        <h3>{doc.title}</h3>
        {ref.summary && (
          <HighlightableText
            as="p"
            text={ref.summary}
            contentId={doc.definition_id}
            contentVersion={doc.content_version}
            anchor="summary"
            highlights={highlights}
          />
        )}
        {(ref.sections ?? []).map((s, i) => (
          <section key={i}>
            <h5>{s.kind.replace(/_/g, " ")}</h5>
            <HighlightableText
              text={s.body}
              contentId={doc.definition_id}
              contentVersion={doc.content_version}
              anchor={`section:${i}`}
              highlights={highlights}
            />
          </section>
        ))}
        {(ref.media ?? []).map((m) =>
          m.media_type.startsWith("image/") ? (
            // eslint-disable-next-line @next/next/no-img-element -- pack media by URI, not optimizable
            <img key={m.uri} src={m.uri} alt={m.alt} className="media" />
          ) : (
            <a key={m.uri} href={m.uri} target="_blank" rel="noreferrer">
              {m.alt}
            </a>
          ),
        )}
      </article>
    );
  }
  // skill and any other kind: show the document's text fields generically.
  const d = doc.document;
  return (
    <article>
      <h3>{doc.title}</h3>
      {Object.entries(d)
        .filter(([k, v]) => typeof v === "string" && !["id", "version", "title"].includes(k))
        .map(([k, v]) => (
          <section key={k}>
            <h5>{k.replace(/_/g, " ")}</h5>
            <HighlightableText
              text={v as string}
              contentId={doc.definition_id}
              contentVersion={doc.content_version}
              anchor={k}
              highlights={highlights}
            />
          </section>
        ))}
    </article>
  );
}
