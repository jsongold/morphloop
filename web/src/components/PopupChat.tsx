"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "@/lib/api";
import type { ChatEvent, ChatExchange, HighlightEvent, MemoView } from "@/lib/types";
import { References } from "./ChatPanel";
import { HighlightableText } from "./HighlightableText";

export const POPUP_WIDTH = 320;

interface Props {
  sessionId: string;
  highlight: HighlightEvent;
  /** Screen anchor for the popup; placed just right of it, clamped to the viewport. */
  anchor: { x: number; y: number };
  highlights: HighlightEvent[];
  highlightsById: Map<string, HighlightEvent>;
  onClose: () => void;
  onMemo: (memo: MemoView) => void;
  onSend: (text: string, threadId: string) => Promise<ChatExchange>;
}

function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

/**
 * Popup chat for one highlight thread (`thr_<tail>` of `hl_<tail>`). Opens to
 * the right of the selection anchor, loads the thread, and sends messages that
 * quote the highlight. A summarizer may return a memo (`memo_<tail>`), which is
 * forwarded to the memo pane.
 */
export function PopupChat({
  sessionId,
  highlight,
  anchor,
  highlights,
  highlightsById,
  onClose,
  onMemo,
  onSend,
}: Props) {
  const threadId = `thr_${highlight.payload.highlight_id.replace(/^hl_/, "")}`;
  const [log, setLog] = useState<ChatEvent[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getSessionChat(sessionId, threadId);
        if (!cancelled) setLog(res.events);
      } catch (e) {
        if (!cancelled) setError(errText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, threadId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [log.length]);

  const placement = useMemo(() => {
    const M = 8;
    let left = anchor.x;
    if (left + POPUP_WIDTH + M > window.innerWidth) left = Math.max(M, anchor.x - POPUP_WIDTH - M);
    left = Math.min(Math.max(left, M), Math.max(M, window.innerWidth - POPUP_WIDTH - M));
    const top = Math.min(Math.max(anchor.y, M), Math.max(M, window.innerHeight - 60 - M));
    return { left, top };
  }, [anchor]);

  const submit = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    setError(null);
    try {
      const res = await onSend(t, threadId);
      if (res.memo) onMemo(res.memo);
      setLog((prev) => {
        const ids = new Set(prev.map((e) => e.event_id));
        return [...prev, ...[res.request, res.reply].filter((e) => !ids.has(e.event_id))];
      });
      setText("");
    } catch (e) {
      setError(`${errText(e)} — send again to retry.`);
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className="popup-chat"
      style={{ left: placement.left, top: placement.top }}
      role="dialog"
      aria-label="Chat about this highlight"
    >
      <div className="popup-chat-head">
        <blockquote className="flash">{highlight.payload.selected_text}</blockquote>
        <button className="popup-chat-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="chat-log" ref={logRef} aria-live="polite">
        {log.length === 0 && <p className="muted">Ask the tutor about this highlight.</p>}
        {log.map((e) => (
          <div
            key={e.event_id}
            className={`chat-msg ${e.event_type === "assistant.message_generated" ? "tutor" : "learner"}`}
          >
            <div className="chat-meta">
              {e.event_type === "assistant.message_generated" ? `tutor · ${e.payload.mode}` : "you"}
            </div>
            <References refs={e.payload.references} highlightsById={highlightsById} />
            <HighlightableText
              text={e.payload.text}
              contentId={e.payload.message_id}
              contentVersion=""
              sourceKind="chat_message"
              highlights={highlights}
            />
          </div>
        ))}
        {sending && <p className="muted">Tutor is replying…</p>}
      </div>
      {error && <p className="error">{error}</p>}
      <form
        className="chat-form"
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit();
        }}
      >
        <textarea
          value={text}
          rows={2}
          placeholder="Ask about this highlight… (Enter to send, Shift+Enter for newline)"
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