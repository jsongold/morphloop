"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { StoredEvent } from "@/v2/types";
import { renderMarkdown } from "./markdown";
import { answerRequest, drillPath, type DrillItem, type Submission } from "./request";

export default function DrillPane() {
  const { ws } = useWorkspace();
  return <Drills key={ws?.ws_id ?? "none"} wsId={ws?.ws_id ?? null} />;
}

function Drills({ wsId }: { wsId: string | null }) {
  const [filter, setFilter] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [items, setItems] = useState<DrillItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const pending = useRef<Record<string, Submission>>({});

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

  async function submit(item: DrillItem, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!wsId || busy) return;
    const request = answerRequest(wsId, item, values[item.id] ?? "", pending.current[item.id] ?? null, newIdempotencyKey);
    if (!request) return;
    pending.current[item.id] = request;
    setBusy(item.id);
    setError(null);
    try {
      if ("artifact_id" in request.body) {
        const artifact = await get<{ spec_id: string }>(`/ws/${encodeURIComponent(wsId)}/artifacts/${encodeURIComponent(request.body.artifact_id)}`);
        if (item.artifact_ref && artifact.spec_id !== item.artifact_ref) {
          throw new Error("That artifact belongs to a different lab.");
        }
      }
      const { body } = await post<StoredEvent>(
        `/ws/${encodeURIComponent(wsId)}/drills/${encodeURIComponent(item.id)}/answers`,
        request.body,
        request.key,
      );
      setAnswers((previous) => ({ ...previous, [item.id]: body.id }));
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
          {items.map((item) => (
            <li key={item.id} style={{ marginBottom: 12 }}>
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(item.question) }} />
              {answers[item.id] ? <p className="muted" role="status">Answered</p> : (
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
                    <>
                      <p className="muted">Finish the lab first. Started artifacts cannot be listed yet; enter its ID if you have it.</p>
                      <label>Artifact ID from this workspace<br />
                        <input value={values[item.id] ?? ""} required pattern="art_[0-9A-Za-z]{1,64}" onChange={(event) => setValues((v) => ({ ...v, [item.id]: event.target.value }))} placeholder="art_…" />
                      </label>
                    </>
                  ) : (
                    <label>
                      Your answer<br />
                      <textarea value={values[item.id] ?? ""} maxLength={20000} onChange={(event) => setValues((v) => ({ ...v, [item.id]: event.target.value }))} />
                    </label>
                  )}
                  <div><button type="submit" disabled={!wsId || busy !== null || !(values[item.id] ?? "").trim()}>{busy === item.id ? "Submitting…" : "Submit answer"}</button></div>
                </form>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
