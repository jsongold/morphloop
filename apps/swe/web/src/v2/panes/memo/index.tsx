"use client";

import { useEffect, useState } from "react";
import { get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import { attemptFor, type Attempt } from "./attempt";

type Entry = {
  entry_id: string;
  actor: "learner" | "assistant";
  body: string;
  position: number;
};

export default function MemoPane() {
  const { ws } = useWorkspace();
  const [loaded, setLoaded] = useState<{ wsId: string; entries: Entry[] } | null>(null);
  const [body, setBody] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const entries = loaded && loaded.wsId === ws?.ws_id ? loaded.entries : [];

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    get<{ entries: Entry[] }>(`/ws/${ws.ws_id}/memo/entries`).then(
      ({ entries }) => { if (!cancelled) { setLoaded({ wsId: ws.ws_id, entries }); setError(null); } },
      (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [ws]);

  async function append() {
    const value = body.trim();
    if (!ws || !value || value.length > 4000 || saving) return;
    const attempt = attemptFor(pending, ws.ws_id, value, newIdempotencyKey);
    setPending(attempt);
    setSaving(true);
    setError(null);
    try {
      await post(`/ws/${ws.ws_id}/memo/entries`, { actor: "learner", body: value }, attempt.key);
      setPending(null);
      setBody("");
      const result = await get<{ entries: Entry[] }>(`/ws/${ws.ws_id}/memo/entries`);
      setLoaded({ wsId: ws.ws_id, entries: result.entries });
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
      {entries.length === 0 && <p className="muted">No notes yet.</p>}
      {entries.map((entry) => (
        <div key={entry.entry_id} className="memo-card">
          <small className="muted">{entry.actor}</small>
          <p className="memo-body">{entry.body}</p>
        </div>
      ))}
      {error && <p className="error" role="alert">{error}</p>}
      <form onSubmit={(e) => { e.preventDefault(); void append(); }}>
        <textarea value={body} maxLength={4000} rows={3} aria-label="New note" placeholder="Write a note…" onChange={(e) => setBody(e.target.value)} />
        <button type="submit" disabled={saving || !body.trim()}>{saving ? "Saving…" : "Add note"}</button>
      </form>
    </section>
  );
}
