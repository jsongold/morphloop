"use client";

import { useEffect, useRef, useState } from "react";
import { get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { StoredEvent } from "@/v2/types";
import {
  attemptFor,
  canRetryLoad,
  canSend,
  clearIfUnchanged,
  isHintThread,
  keyForWorkspace,
  mergeMessages,
  type Attempt,
  type Message,
} from "./attempt";

type ThreadCreated = StoredEvent<{ thread_id: string; labels?: string[] }>;
export default function ChatPane() {
  const { ws, threadId, setThread } = useWorkspace();
  const [main, setMain] = useState<ThreadCreated | null>(null);
  const [loaded, setLoaded] = useState<{ path: string; messages: Message[] } | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [openError, setOpenError] = useState<{ wsId: string; message: string } | null>(null);
  const [openRetry, setOpenRetry] = useState(0);
  const [loadRetry, setLoadRetry] = useState(0);
  // Labels of every thread this pane has itself created or loaded, keyed by
  // thread_id (currently only ever the main thread we posted for this ws).
  const [threadLabels, setThreadLabels] = useState<Map<string, string[] | undefined>>(new Map());
  const mainKeys = useRef(new Map<string, string>());
  const loadVersion = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);
  const mainId = main && main.ws_id === ws?.ws_id ? main.payload.thread_id : null;
  const activeId = threadId ?? mainId;
  const isHint = isHintThread(threadLabels, activeId);
  const path = ws && activeId ? `/ws/${ws.ws_id}/threads/${activeId}/messages` : null;
  const messages = loaded && loaded.path === path ? loaded.messages : null;
  const loadedPath = loaded?.path ?? null;
  const openingError = openError && openError.wsId === ws?.ws_id ? openError.message : null;
  const loadFailed = canRetryLoad(loadedPath, path, error);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    const key = keyForWorkspace(mainKeys.current, ws.ws_id, newIdempotencyKey);
    post<ThreadCreated>(`/ws/${ws.ws_id}/threads`, {}, key).then(
      ({ body }) => {
        if (cancelled) return;
        setMain(body);
        setOpenError(null);
        setThreadLabels((prev) => new Map(prev).set(body.payload.thread_id, body.payload.labels));
      },
      (e: unknown) => { if (!cancelled) setOpenError({ wsId: ws.ws_id, message: e instanceof Error ? e.message : String(e) }); },
    );
    return () => { cancelled = true; };
  }, [ws, openRetry]);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    const version = ++loadVersion.current;
    get<{ messages: Message[] }>(path).then(
      ({ messages }) => { if (!cancelled && version === loadVersion.current) { setLoaded({ path, messages }); setError(null); } },
      (e: unknown) => { if (!cancelled && version === loadVersion.current) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [path, loadRetry]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [loaded]);

  async function send() {
    const value = text.trim();
    if (!path || !canSend(loadedPath, path, value, sending)) return;
    const draft = text;
    const attempt = attemptFor(pending, path, value, newIdempotencyKey);
    const version = ++loadVersion.current;
    setPending(attempt);
    setSending(true);
    setError(null);
    try {
      const { body } = await post<{ sent: Message; reply: Message }>(path, { text: value }, attempt.key);
      if (version === loadVersion.current) {
        setLoaded((current) => ({
          path,
          messages: mergeMessages(current?.path === path ? current.messages : [], [body.sent, body.reply]),
        }));
      }
      setPending(null);
      setText((current) => clearIfUnchanged(current, draft));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      try {
        const result = await get<{ messages: Message[] }>(path);
        if (version === loadVersion.current) setLoaded({ path, messages: result.messages });
      } catch {
        // Keep the POST result or error; this GET only reconciles the log.
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
        {!activeId && !openingError && <p className="muted">Opening chat…</p>}
        {activeId && messages?.length === 0 && <p className="muted">Ask the tutor a question.</p>}
        {messages?.map((message) => (
          <div key={message.message_id} className={`chat-msg ${message.role === "assistant" ? "tutor" : "learner"}`}>
            <div className="chat-meta">{message.role === "assistant" ? "tutor" : "you"}</div>
            <div style={{ whiteSpace: "pre-wrap" }}>{message.text}</div>
          </div>
        ))}
        {sending && <p className="muted">Tutor is replying…</p>}
      </div>
      {!activeId && openingError && <div><p className="error" role="alert">{openingError}</p><button onClick={() => { setOpenError(null); setOpenRetry((n) => n + 1); }}>Retry opening chat</button></div>}
      {error && (
        <div>
          <p className="error" role="alert">{error}</p>
          {loadFailed && <button onClick={() => { setError(null); setLoadRetry((n) => n + 1); }}>Retry loading chat</button>}
        </div>
      )}
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
        <button type="submit" disabled={!canSend(loadedPath, path, text, sending)}>
          {pending?.path === path && pending.text === text.trim() && error ? "Resend" : "Send"}
        </button>
      </form>
    </section>
  );
}
