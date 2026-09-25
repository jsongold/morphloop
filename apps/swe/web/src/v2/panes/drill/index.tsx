"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { StoredEvent } from "@/v2/types";
import { renderMarkdown } from "./markdown";
import {
  answeredItems,
  answerRequest,
  artifactOptions,
  artifactsPath,
  answersPath,
  drillArtifactRefs,
  drillPath,
  type Artifact,
  type DrillAnswer,
  type DrillItem,
  type Submission,
} from "./request";

export default function DrillPane() {
  const { ws } = useWorkspace();
  return <Drills key={ws?.ws_id ?? "none"} wsId={ws?.ws_id ?? null} />;
}

function Drills({ wsId }: { wsId: string | null }) {
  const [filter, setFilter] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [items, setItems] = useState<DrillItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<{ wsId: string; byItem: Record<string, string> } | null>(null);
  const [artifacts, setArtifacts] = useState<Record<string, Artifact[]>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const pending = useRef<Record<string, Submission>>({});
  const answered = answers && answers.wsId === wsId ? answers.byItem : {};

  useEffect(() => {
    let cancelled = false;
    get<{ items: DrillItem[] }>(drillPath(labels)).then(
      (data) => {
        if (!cancelled) {
          setItems(data.items);
          setError(null);
        }
      },
      (e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => { cancelled = true; };
  }, [labels]);

  useEffect(() => {
    if (!wsId) return;
    let cancelled = false;
    get<{ items: DrillAnswer[] }>(answersPath(wsId)).then(
      (data) => { if (!cancelled) setAnswers({ wsId, byItem: answeredItems(data.items) }); },
      (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [wsId]);

  useEffect(() => {
    const refs = drillArtifactRefs(items ?? []);
    if (!wsId || refs.length === 0) return;
    let cancelled = false;
    Promise.all(
      refs.map((ref) => get<{ items: Artifact[] }>(artifactsPath(wsId), { spec_id: ref }).then((data) => [ref, data.items] as const)),
    ).then(
      (entries) => { if (!cancelled) setArtifacts(Object.fromEntries(entries)); },
      (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { cancelled = true; };
  }, [wsId, items]);

  async function submit(item: DrillItem, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!wsId || busy) return;
    const request = answerRequest(wsId, item, values[item.id] ?? "", pending.current[item.id] ?? null, newIdempotencyKey);
    if (!request) return;
    pending.current[item.id] = request;
    setBusy(item.id);
    setError(null);
    try {
      const { body } = await post<StoredEvent>(
        `/ws/${encodeURIComponent(wsId)}/drills/${encodeURIComponent(item.id)}/answers`,
        request.body,
        request.key,
      );
      setAnswers((previous) => ({
        wsId,
        byItem: { ...(previous?.wsId === wsId ? previous.byItem : {}), [item.id]: body.id },
      }));
      delete pending.current[item.id];
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-label="Drills">
      <h2>Drills</h2>
      <form className="row" onSubmit={(event) => {
        event.preventDefault();
        setItems(null);
        setError(null);
        setLabels(filter.split(/[\s,]+/).filter(Boolean));
      }}>
        <label htmlFor="drill-labels">Labels</label>
        <input id="drill-labels" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="topic:network.dns, origin:pack" />
        <button type="submit">Filter</button>
      </form>
      {!wsId && <p className="muted">Select a workspace to answer drills.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {items === null ? (!error && <p className="muted">Loading drills…</p>) : items.length === 0 ? (
        <p className="muted">No drills found.</p>
      ) : (
        <ol>
          {items.map((item) => {
            const options = artifactOptions(artifacts[item.artifact_ref ?? ""] ?? [], item.artifact_ref);
            return (
              <li key={item.id} style={{ marginBottom: 12 }}>
                <div dangerouslySetInnerHTML={{ __html: renderMarkdown(item.question) }} />
                {answered[item.id] ? <p className="muted" role="status">Answered</p> : (
                  <form onSubmit={(event) => void submit(item, event)}>
                    {item.answer_mode === "choice" ? (
                      <fieldset>
                        <legend>Choose an answer</legend>
                        {item.choices?.map((choice, index) => (
                          <div key={choice} className="row">
                            <input type="radio" name={`drill-${item.id}`} value={choice} aria-labelledby={`drill-${item.id}-choice-${index}`} checked={values[item.id] === choice} onChange={() => setValues((v) => ({ ...v, [item.id]: choice }))} />
                            <div id={`drill-${item.id}-choice-${index}`} dangerouslySetInnerHTML={{ __html: renderMarkdown(choice) }} />
                          </div>
                        ))}
                      </fieldset>
                    ) : item.answer_mode === "artifact" ? (
                      options.length === 0 ? <p className="muted">Start the lab first.</p> : (
                        <label>
                          Your artifact<br />
                          <select value={values[item.id] ?? ""} required onChange={(event) => setValues((v) => ({ ...v, [item.id]: event.target.value }))}>
                            <option value="" disabled>Choose an artifact…</option>
                            {options.map((artifact) => (
                              <option key={artifact.artifact_id} value={artifact.artifact_id}>{artifact.artifact_id} ({artifact.status})</option>
                            ))}
                          </select>
                        </label>
                      )
                    ) : (
                      <label>
                        Your answer<br />
                        <textarea value={values[item.id] ?? ""} maxLength={20000} onChange={(event) => setValues((v) => ({ ...v, [item.id]: event.target.value }))} />
                      </label>
                    )}
                    {!(item.answer_mode === "artifact" && options.length === 0) && (
                      <div><button type="submit" disabled={!wsId || busy !== null || !(values[item.id] ?? "").trim()}>{busy === item.id ? "Submitting…" : "Submit answer"}</button></div>
                    )}
                  </form>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
