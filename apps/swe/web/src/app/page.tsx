"use client";

import { useSyncExternalStore } from "react";
import { SessionPicker } from "@/components/SessionPicker";
import { Workspace } from "@/components/Workspace";

// v0.1 has a single local learner and no auth: the learner and the current
// session id are remembered in the browser; everything else is resumed from
// the API (AC-F4).
const LEARNER_KEY = "morphloop.learner_id";
const SESSION_KEY = "morphloop.session_id";

const listeners = new Set<() => void>();
function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // storage unavailable: state lives until reload
  }
  listeners.forEach((l) => l());
}
function useStored(key: string): string | null | undefined {
  // undefined on the server / before hydration.
  return useSyncExternalStore(subscribe, () => read(key), () => undefined);
}

export default function Home() {
  const learnerId = useStored(LEARNER_KEY);
  const sessionId = useStored(SESSION_KEY);

  if (learnerId === undefined || sessionId === undefined) {
    return <div className="center muted">Loading…</div>;
  }
  if (sessionId) {
    return <Workspace key={sessionId} sessionId={sessionId} onLeave={() => write(SESSION_KEY, null)} />;
  }
  return (
    <SessionPicker
      learnerId={learnerId}
      onLearner={(id) => write(LEARNER_KEY, id)}
      onSession={(id) => write(SESSION_KEY, id)}
    />
  );
}
