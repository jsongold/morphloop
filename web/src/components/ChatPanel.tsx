"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatEvent, ChatReference, HighlightEvent } from "@/lib/types";

interface Props {
  events: ChatEvent[];
  highlightsById: Map<string, HighlightEvent>;
  /** Highlights quoted in the composer (from "Ask AI"). */
  quoted: HighlightEvent[];
  onRemoveQuote: (highlightId: string) => void;
  onSend: (text: string) => Promise<void>;
  error: string | null;
}

/** Persistent tutor chat. Referenced highlights are rendered as visible quotes (AC-D3, AC-D4). */
export function ChatPanel({ events, highlightsById, quoted, onRemoveQuote, onSend, error }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  useEffect(() => {
    if (quoted.length > 0) inputRef.current?.focus();
  }, [quoted.length]);

  const submit = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    try {
      await onSend(t);
      setText("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="chat">
      <div className="chat-log" ref={logRef} aria-live="polite">
        {events.length === 0 && <p className="muted">Ask the tutor about the mission, a command or a highlight.</p>}
        {events.map((e) => (
          <div key={e.event_id} className={`chat-msg ${e.event_type === "assistant.message_generated" ? "tutor" : "learner"}`}>
            <div className="chat-meta">
              {e.event_type === "assistant.message_generated" ? `tutor · ${e.payload.mode}` : "you"}
            </div>
            <References refs={e.payload.references} highlightsById={highlightsById} />
            <div style={{ whiteSpace: "pre-wrap" }}>{e.payload.text}</div>
          </div>
        ))}
        {sending && <p className="muted">Tutor is replying…</p>}
      </div>
      {quoted.length > 0 && (
        <div className="chat-quotes">
          {quoted.map((h) => (
            <blockquote key={h.payload.highlight_id}>
              {h.payload.selected_text}{" "}
              <button className="link" aria-label="Remove quote" onClick={() => onRemoveQuote(h.payload.highlight_id)}>
                ×
              </button>
            </blockquote>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <form
        className="chat-form"
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit();
        }}
      >
        <textarea
          ref={inputRef}
          value={text}
          rows={2}
          placeholder="Ask the tutor… (Enter to send, Shift+Enter for newline)"
          onChange={(ev) => setText(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && !ev.shiftKey && !ev.nativeEvent.isComposing) {
              ev.preventDefault();
              void submit();
            }
          }}
        />
        <button type="submit" disabled={sending || !text.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

function References({
  refs,
  highlightsById,
}: {
  refs: ChatReference[];
  highlightsById: Map<string, HighlightEvent>;
}) {
  if (refs.length === 0) return null;
  return (
    <>
      {refs.map((r) => {
        if (r.type === "highlight") {
          const h = highlightsById.get(r.id);
          return (
            <blockquote key={r.id} className="chat-cite">
              {h ? h.payload.selected_text : `highlight ${r.id}`}
            </blockquote>
          );
        }
        return (
          <div key={r.id} className="chat-cite muted">
            event {r.id}
          </div>
        );
      })}
    </>
  );
}
