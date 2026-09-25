"use client";

import { useEffect, useState } from "react";
import { get, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { Session, TopicNode } from "@/v2/types";
import { newestSessions, sessionWs, startSession } from "./flow";

interface TopicsResponse {
  pack_id: string;
  pack_hash: string;
  topics: TopicNode[];
}

function TopicList({ topics, start, busy }: {
  topics: TopicNode[];
  start: (id: string) => void;
  busy: boolean;
}) {
  return (
    <ul style={{ paddingLeft: 16 }}>
      {topics.map((topic) => (
        <li key={topic.id}>
          <button disabled={busy} onClick={() => start(topic.id)}>Start {topic.title}</button>
          {topic.description && <p className="muted">{topic.description}</p>}
          {topic.topics && <TopicList topics={topic.topics} start={start} busy={busy} />}
        </li>
      ))}
    </ul>
  );
}

export default function SessionPane() {
  const { session, ws, error, setSession, setWs } = useWorkspace();
  const [topics, setTopics] = useState<TopicsResponse | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (session) return;
    let cancelled = false;
    get<TopicsResponse>("/topics").then(
      (result) => { if (!cancelled) setTopics(result); },
      (e: unknown) => { if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e)); },
    );
    get<Session[]>("/sessions").then(
      (result) => { if (!cancelled) setSessions(newestSessions(result)); },
      (e: unknown) => { if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [session]);

  const open = async (choice: () => Promise<Session>) => {
    if (busy) return;
    setBusy(true);
    setLoadError(null);
    try {
      const selected = await choice();
      setSessions((current) => current && newestSessions([selected, ...current.filter((s) => s.id !== selected.id)]));
      const selectedWs = await sessionWs(selected.id, get, post);
      setSession(selected);
      setWs(selectedWs);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (session) {
    return (
      <>
        <strong>{session.pack_id}</strong>
        <span className="muted">
          v{session.pack_version} · {session.tree.title} · {session.id}
          {ws ? ` · ${ws.ws_id}` : ""}
        </span>
        <button className="link" onClick={() => setSession(null)}>Sessions</button>
      </>
    );
  }
  return (
    <>
      <h1>morphloop v2</h1>
      {(error ?? loadError) && <p className="error" role="alert">{error ?? loadError}</p>}
      <section>
        <h2>Start a session</h2>
        {topics === null ? <p className="muted">Loading topics…</p> : topics.topics.length === 0 ? (
          <p className="muted">No topics in this pack.</p>
        ) : (
          <TopicList topics={topics.topics} busy={busy} start={(topic_id) =>
            void open(() => startSession(topics.pack_id, topic_id, post))} />
        )}
      </section>
      <section>
        <h2>Resume</h2>
        {sessions === null ? <p className="muted">Loading sessions…</p> : sessions.length === 0 ? (
          <p className="muted">No sessions yet.</p>
        ) : (
          <ul>
            {sessions.map((s) => (
              <li key={s.id}>
                <button disabled={busy} onClick={() => void open(async () => s)}>Open {s.tree.title}</button>{" "}
                <span className="muted">{s.pack_id} v{s.pack_version} · {s.topic_id} · {new Date(s.created_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
