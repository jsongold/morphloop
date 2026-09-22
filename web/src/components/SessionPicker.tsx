"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import { newIdempotencyKey } from "@/lib/ids";
import type { Health, Pack, Session } from "@/lib/types";

interface Props {
  learnerId: string | null;
  onLearner: (learnerId: string) => void;
  onSession: (sessionId: string) => void;
}

/** Start a session on an imported pack, or resume one of the learner's sessions (AC-F4). */
export function SessionPicker({ learnerId, onLearner, onSession }: Props) {
  const [health, setHealth] = useState<Health | "down" | null>(null);
  const [packs, setPacks] = useState<Pack[] | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getHealth()
      .then((h) => !cancelled && setHealth(h))
      .catch(() => !cancelled && setHealth("down"));
    api
      .listPacks()
      .then((r) => !cancelled && setPacks(r.packs))
      .catch((e) => !cancelled && setError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!learnerId) return;
    let cancelled = false;
    api
      .listLearnerSessions(learnerId)
      .then((r) => !cancelled && setSessions(r.sessions))
      .catch(() => !cancelled && setSessions([]));
    return () => {
      cancelled = true;
    };
  }, [learnerId]);

  const start = async (p: Pack) => {
    setBusy(true);
    setError(null);
    try {
      let id = learnerId;
      if (!id) {
        id = (await api.createLearner()).learner_id;
        onLearner(id);
      }
      const res = await api.startSession({
        idempotency_key: newIdempotencyKey(),
        learner_id: id,
        pack_id: p.pack.pack_id,
        pack_content_hash: p.pack.pack_content_hash,
      });
      onSession(res.body.session.session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="picker">
      <h1>morphloop</h1>
      <p className="muted">
        API {api.API_BASE_URL}:{" "}
        {health === null ? "checking…" : health === "down" ? "unreachable" : `${health.status} (db ${health.db})`}
      </p>
      {error && <p className="error">{error}</p>}

      {sessions && sessions.length > 0 && (
        <section>
          <h2>Resume</h2>
          <ul>
            {sessions.map((s) => (
              <li key={s.session_id}>
                <button onClick={() => onSession(s.session_id)}>Resume</button>{" "}
                {s.pack.pack_id} v{s.pack.pack_version}{" "}
                <span className="muted">started {new Date(s.started_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2>Start a session</h2>
        {packs === null && !error && <p className="muted">Loading packs…</p>}
        {packs?.length === 0 && <p className="muted">No pack is imported yet.</p>}
        <ul>
          {packs?.map((p) => (
            <li key={p.pack.pack_content_hash}>
              <button disabled={busy} onClick={() => void start(p)}>
                Start
              </button>{" "}
              <strong>{p.title}</strong>{" "}
              <span className="muted">
                {p.pack.pack_id} v{p.pack.pack_version} · {p.pack.pack_content_hash.slice(0, 19)}…
              </span>
            </li>
          ))}
        </ul>
      </section>
      {learnerId && <p className="muted">learner {learnerId}</p>}
    </div>
  );
}
