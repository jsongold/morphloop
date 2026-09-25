"use client";

// Owned by the session pane PR; replace this directory. Rendered in two
// places (src/v2/panes/types.ts): as the picker when no session is selected
// and in the header once one is. This stub only lists and reopens sessions.

import { useEffect, useState } from "react";
import { get } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { Session } from "@/v2/types";

export default function SessionPane() {
  const { session, ws, error, setSession } = useWorkspace();
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (session) return;
    get<Session[]>("/sessions").then(setSessions, (e: unknown) =>
      setLoadError(e instanceof Error ? e.message : String(e)),
    );
  }, [session]);

  if (session) {
    return (
      <>
        <strong>{session.pack_id}</strong>
        <span className="muted">
          v{session.pack_version} · {session.topic_id} · {session.id}
          {ws ? ` · ${ws.ws_id}` : ""}
        </span>
        <button className="link" onClick={() => setSession(null)}>
          Sessions
        </button>
      </>
    );
  }
  return (
    <>
      <h1>morphloop v2</h1>
      {(error ?? loadError) && (
        <p className="error" role="alert">
          {error ?? loadError}
        </p>
      )}
      <p className="muted">session pane: creating a session is not implemented</p>
      {sessions === null ? (
        <p className="muted">Loading sessions…</p>
      ) : sessions.length === 0 ? (
        <p className="muted">No sessions yet.</p>
      ) : (
        <ul>
          {sessions.map((s) => (
            <li key={s.id}>
              <button className="link" onClick={() => setSession(s)}>
                {s.topic_id}
              </button>{" "}
              <span className="muted">{s.id}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
