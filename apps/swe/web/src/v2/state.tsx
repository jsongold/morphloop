"use client";

// Shared state of the /v2 workspace: the current session and ws, the
// selected textbook doc and the active chat thread. The selection lives in
// the URL query (?session=&ws=&doc=) so a reload restores it; the session and
// ws objects are re-read from the API from those ids. The thread id is
// in-memory only (a pane re-derives it from the ws/highlight it belongs to).

import { createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { get } from "./api";
import type { Session, Ws } from "./types";

export interface WorkspaceState {
  session: Session | null;
  ws: Ws | null;
  docId: string | null;
  threadId: string | null;
  /** Set when restoring the URL's session/ws failed (the ids are then dropped). */
  error: string | null;
  /** Selecting a session clears ws and doc. */
  setSession: (session: Session | null) => void;
  setWs: (ws: Ws | null) => void;
  setDoc: (docId: string | null) => void;
  setThread: (threadId: string | null) => void;
}

const Ctx = createContext<WorkspaceState | null>(null);

// ---- URL query as an external store (same pattern as v0.1's localStorage) ----

const listeners = new Set<() => void>();
function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("popstate", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("popstate", fn);
  };
}
function writeParams(patch: Record<string, string | null>) {
  const u = new URL(window.location.href);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) u.searchParams.delete(k);
    else u.searchParams.set(k, v);
  }
  window.history.replaceState(window.history.state, "", u);
  listeners.forEach((l) => l());
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  // null on the server / before hydration.
  const search = useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => null,
  );
  const params = new URLSearchParams(search ?? "");
  const sessionId = params.get("session");
  const wsId = params.get("ws");
  const docId = params.get("doc");

  const [sessionObj, setSessionObj] = useState<Session | null>(null);
  const [wsObj, setWsObj] = useState<Ws | null>(null);
  const [threadId, setThread] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The URL ids are authoritative; a stale object is simply not shown.
  const session = sessionObj && sessionObj.id === sessionId ? sessionObj : null;
  const ws = wsObj && wsObj.ws_id === wsId ? wsObj : null;

  // Restore the objects behind the URL ids (reload / shared link).
  useEffect(() => {
    if (search === null || !sessionId || session) return;
    let cancelled = false;
    get<Session>(`/sessions/${sessionId}`).then(
      (s) => !cancelled && setSessionObj(s),
      (e: unknown) => {
        if (cancelled) return;
        setError(`session ${sessionId}: ${e instanceof Error ? e.message : String(e)}`);
        writeParams({ session: null, ws: null, doc: null });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [search, sessionId, session]);

  useEffect(() => {
    if (search === null || !wsId || ws) return;
    let cancelled = false;
    get<Ws>(`/ws/${wsId}`).then(
      (w) => !cancelled && setWsObj(w),
      (e: unknown) => {
        if (cancelled) return;
        setError(`ws ${wsId}: ${e instanceof Error ? e.message : String(e)}`);
        writeParams({ ws: null });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [search, wsId, ws]);

  const setSession = useCallback((s: Session | null) => {
    setSessionObj(s);
    setThread(null);
    setError(null);
    writeParams({ session: s?.id ?? null, ws: null, doc: null });
  }, []);
  const setWs = useCallback((w: Ws | null) => {
    setWsObj(w);
    setThread(null);
    writeParams({ ws: w?.ws_id ?? null });
  }, []);
  const setDoc = useCallback((id: string | null) => writeParams({ doc: id }), []);

  if (search === null) return <div className="center muted">Loading…</div>;
  return (
    <Ctx.Provider value={{ session, ws, docId, threadId, error, setSession, setWs, setDoc, setThread }}>
      {children}
    </Ctx.Provider>
  );
}

export function useWorkspace(): WorkspaceState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkspace() must be used under <WorkspaceProvider>");
  return v;
}
