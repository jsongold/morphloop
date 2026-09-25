"use client";

import { useEffect, useRef, useState } from "react";
import { get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import { attemptFor, canAppend, canRetryLoad, clearIfUnchanged, mergeEntries, type Attempt, type Entry } from "./attempt";

export default function MemoPane() {
  const { ws } = useWorkspace();
  const [loaded, setLoaded] = useState<{ wsId: string; entries: Entry[] } | null>(null);
  const [body, setBody] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const loadVersion = useRef(0);

  const entries = loaded && loaded.wsId === ws?.ws_id ? loaded.entries : [];
  const loadedWsId = loaded?.wsId ?? null;
  const loadReady = !!ws && loadedWsId === ws.ws_id;
  const loadFailed = canRetryLoad(loadedWsId, ws?.ws_id ?? null, error);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    const version = ++loadVersion.current;
    get<{ entries: Entry[] }>(`/ws/${ws.ws_id}/memo/entries`).then(
      ({ entries }) => { if (!cancelled && version === loadVersion.current) { setLoaded({ wsId: ws.ws_id, entries }); setError(null); } },
      (e: unknown) => { if (!cancelled && version === loadVersion.current) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [ws, loadRetry]);

  async function append() {
    const value = body.trim();
    if (!ws || !canAppend(loadedWsId, ws.ws_id, value, saving) || value.length > 4000) return;
    const draft = body;
    const version = ++loadVersion.current;
    const attempt = attemptFor(pending, ws.ws_id, value, newIdempotencyKey);
    setPending(attempt);
    setSaving(true);
    setError(null);
    try {
      const { body: entry } = await post<Entry>(`/ws/${ws.ws_id}/memo/entries`, { actor: "learner", body: value }, attempt.key);
      if (version === loadVersion.current) {
        setLoaded((current) => ({
          wsId: ws.ws_id,
          entries: mergeEntries(current?.wsId === ws.ws_id ? current.entries : [], entry),
        }));
      }
      setPending(null);
      setBody((current) => clearIfUnchanged(current, draft));
      try {
        const result = await get<{ entries: Entry[] }>(`/ws/${ws.ws_id}/memo/entries`);
        if (version === loadVersion.current) setLoaded({ wsId: ws.ws_id, entries: result.entries });
      } catch {
        // The POST already returned the stored entry; the local list is current.
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (!ws) return <p className="muted">Select a workspace for notes.</p>;
  return (
    <section className="memo-pane" aria-label="Learning notes">
      <h4>Notes</h4>
      {loadReady && entries.length === 0 && <p className="muted">No notes yet.</p>}
      {!loadReady && !loadFailed && <p className="muted">Loading notes…</p>}
      {entries.map((entry) => (
        <div key={entry.entry_id} className="memo-card">
          <small className="muted">{entry.actor}</small>
          <p className="memo-body">{entry.body}</p>
        </div>
      ))}
      {error && (
        <div>
          <p className="error" role="alert">{error}</p>
          {loadFailed && <button onClick={() => { setError(null); setLoadRetry((n) => n + 1); }}>Retry loading notes</button>}
        </div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); void append(); }}>
        <textarea value={body} maxLength={4000} rows={3} aria-label="New note" placeholder="Write a note…" onChange={(e) => setBody(e.target.value)} />
        <button type="submit" disabled={!canAppend(loadedWsId, ws.ws_id, body, saving)}>{saving ? "Saving…" : "Add note"}</button>
      </form>
    </section>
  );
}
