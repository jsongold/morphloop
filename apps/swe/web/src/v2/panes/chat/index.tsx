"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { StoredEvent } from "@/v2/types";
import { attemptFor, type Attempt } from "./attempt";

type ThreadCreated = StoredEvent<{ thread_id: string; labels?: string[] }>;
type Message = { message_id: string; role: "learner" | "assistant"; text: string; created_at: string };

export default function ChatPane() {
  const { ws, threadId, setThread } = useWorkspace();
  const [main, setMain] = useState<ThreadCreated | null>(null);
  const [loaded, setLoaded] = useState<{ path: string; messages: Message[] } | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const mainId = main && main.ws_id === ws?.ws_id ? main.payload.thread_id : null;
  const activeId = threadId ?? mainId;
  const isHint = activeId === mainId && main?.payload.labels?.includes("mode:hint");
  const path = ws && activeId ? `/ws/${ws.ws_id}/threads/${activeId}/messages` : null;
  const messages = loaded && loaded.path === path ? loaded.messages : null;

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    post<ThreadCreated>(`/ws/${ws.ws_id}/threads`, {}).then(
      ({ body }) => { if (!cancelled) setMain(body); },
      (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [ws]);

  useEffect(() => {
    if (!ws || !activeId) return;
    let cancelled = false;
    get<{ messages: Message[] }>(`/ws/${ws.ws_id}/threads/${activeId}/messages`).then(
      ({ messages }) => { if (!cancelled) { setLoaded({ path: `/ws/${ws.ws_id}/threads/${activeId}/messages`, messages }); setError(null); } },
      (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [ws, activeId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [loaded]);

  async function send() {
    const value = text.trim();
    if (!path || !value || sending) return;
    const attempt = attemptFor(pending, path, value, newIdempotencyKey);
    setPending(attempt);
    setSending(true);
    setError(null);
    try {
      await post(path, { text: value }, attempt.key);
      setPending(null);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (!(e instanceof ApiError && e.status === 502)) return;
    } finally {
      try {
        const result = await get<{ messages: Message[] }>(path);
        setLoaded({ path, messages: result.messages });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
      setSending(false);
    }
  }

  if (!ws) return <p className="muted">Select a workspace to chat.</p>;
  return (
    <section className="chat" aria-label="Chat">
      <div className="row">
        <strong>Chat</strong>
        {isHint && <span className="muted">Hint mode</span>}
        {threadId && <button className="link" onClick={() => setThread(null)}>Main chat</button>}
      </div>
      <div className="chat-log" ref={logRef} aria-live="polite">
        {!activeId && <p className="muted">Opening chat…</p>}
        {activeId && messages?.length === 0 && <p className="muted">Ask the tutor a question.</p>}
        {messages?.map((message) => (
          <div key={message.message_id} className={`chat-msg ${message.role === "assistant" ? "tutor" : "learner"}`}>
            <div className="chat-meta">{message.role === "assistant" ? "tutor" : "you"}</div>
            <div style={{ whiteSpace: "pre-wrap" }}>{message.text}</div>
          </div>
        ))}
        {sending && <p className="muted">Tutor is replying…</p>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <textarea
          value={text}
          rows={2}
          aria-label="Message"
          placeholder="Ask the tutor…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button type="submit" disabled={!activeId || sending || !text.trim()}>
          {pending?.path === path && pending.text === text.trim() && error ? "Resend" : "Send"}
        </button>
      </form>
    </section>
  );
}
