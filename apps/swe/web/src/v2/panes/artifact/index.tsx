"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import TerminalPane, { type TerminalHandle } from "@/components/TerminalPane";
import { get, post } from "../../api";
import { useWorkspace } from "../../state";
import type { ArtifactDirective } from "../types";
import specs from "./specs.json";

export type ArtifactRenderer = (directive: ArtifactDirective) => ReactNode;
const renderers = new Map<string, ArtifactRenderer>();
export function registerArtifactRenderer(type: string, renderer: ArtifactRenderer): void { renderers.set(type, renderer); }
export function ArtifactSlot({ directive }: { directive: ArtifactDirective }) {
  return renderers.get(directive.type)?.(directive) ?? <p className="muted">Unsupported artifact: {directive.type}</p>;
}

type Artifact = { artifact_id: string; type: string; spec_id: string; ws_id: string; status: "running" | "stopped"; lab: { lab_instance_id: string } | null };
type Check = { artifact_id: string; check_id: string; passed: boolean; observed: Record<string, unknown> };
type Active = { artifact: Artifact; result: Check | null } | null;
// ponytail: one active lab per page; keep per-artifact state if a pack embeds several labs.
let active: Active = null;
const listeners = new Set<() => void>();
function publish(next: Active) { active = next; listeners.forEach((listener) => listener()); }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function useActive() { return useSyncExternalStore(subscribe, () => active, () => null); }

function LabView({ directive }: { directive: ArtifactDirective }) {
  const { ws } = useWorkspace();
  const current = useActive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = current?.artifact.ws_id === ws?.ws_id && current?.artifact.spec_id === directive.ref;
  async function start() {
    if (!ws || busy) return;
    setBusy(true); setError(null);
    try {
      const { body } = await post<Artifact>(`/ws/${ws.ws_id}/artifacts`, { spec_id: directive.ref });
      publish({ artifact: body, result: null });
      sessionStorage.setItem(`artifact:${ws.ws_id}:${directive.ref}`, body.artifact_id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  return <div className="row"><strong>Lab</strong> <span role="status">{selected ? current?.artifact.status : "not started"}</span>
    <button onClick={start} disabled={!ws || busy || (selected && current?.artifact.status === "running")}>Start lab</button>
    {error && <span role="alert">{error}</span>}
  </div>;
}

function DiagramView({ directive }: { directive: ArtifactDirective }) {
  const [selected, setSelected] = useState(0);
  if (directive.ref !== specs.diagram.id) return <p role="alert">Diagram {directive.ref} is unavailable.</p>;
  const { title, diagram } = specs.diagram.spec;
  const x = (id: string) => (diagram.actors.findIndex((actor) => actor.id === id) + 0.5) * 190;
  const step = diagram.steps[selected];
  return <section className="viz"><h3>{title}</h3>
    <svg className="viz-svg" viewBox={`0 0 ${diagram.actors.length * 190} ${40 + diagram.steps.length * 45}`} role="img" aria-label={`Sequence diagram: ${title}`}>
      <defs><marker id="v2-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor" /></marker></defs>
      {diagram.actors.map((actor) => <g key={actor.id}><text x={x(actor.id)} y={18} textAnchor="middle" className="viz-actor">{actor.label}</text><line x1={x(actor.id)} x2={x(actor.id)} y1={25} y2={30 + diagram.steps.length * 45} className="viz-lifeline" /></g>)}
      {diagram.steps.map((item, i) => <g key={item.id} className="viz-step">{item.from === item.to
        ? <path d={`M${x(item.from)},${44 + i * 45} h30 v12 h-30`} fill="none" markerEnd="url(#v2-diagram-arrow)" />
        : <line x1={x(item.from)} x2={x(item.to)} y1={50 + i * 45} y2={50 + i * 45} markerEnd="url(#v2-diagram-arrow)" />}
        <text x={(x(item.from) + x(item.to)) / 2} y={43 + i * 45} textAnchor="middle">{item.label}</text></g>)}
    </svg>
    <ol className="viz-steps" aria-label="Diagram steps">{diagram.steps.map((item, i) => <li key={item.id}><button className={i === selected ? "link selected" : "link"} aria-pressed={i === selected} onClick={() => setSelected(i)}>{item.label}</button></li>)}</ol>
    <div className="viz-detail" aria-live="polite"><h4>{step.label}</h4><p>{step.explanation}</p>
      {"reality" in step && step.reality && <><h5>Real mechanism</h5><p>{step.reality.mechanism}</p><h5>Observe it</h5><ul>{step.reality.observe.map((item, i) => <li key={i}><code>{item.argv.join(" ")}</code> — {item.purpose}{"look_for" in item && <p>Look for: {item.look_for}</p>}</li>)}</ul>
        {"artifacts" in step.reality && step.reality.artifacts && <><h5>Observable state</h5><ul>{step.reality.artifacts.map((item, i) => <li key={i}><code>{item.locator}</code> ({item.kind}) — {item.description}</li>)}</ul></>}
      </>}
    </div>
  </section>;
}
registerArtifactRenderer("lab", (directive) => <LabView directive={directive} />);
registerArtifactRenderer("diagram", (directive) => <DiagramView directive={directive} />);

export default function ArtifactPane() {
  const { ws } = useWorkspace();
  const current = useActive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkId, setCheckId] = useState<string>(specs.lab.checks[0].id);
  const terminalHandle = useRef<TerminalHandle | null>(null);
  useEffect(() => {
    if (!ws || current?.artifact.ws_id === ws.ws_id) return;
    const id = sessionStorage.getItem(`artifact:${ws.ws_id}:${specs.lab.id}`);
    if (!id) return;
    let cancelled = false;
    get<Artifact>(`/ws/${ws.ws_id}/artifacts/${id}`).then(
      (artifact) => { if (!cancelled) publish({ artifact, result: null }); },
      () => { if (!cancelled) sessionStorage.removeItem(`artifact:${ws.ws_id}:${specs.lab.id}`); },
    );
    return () => { cancelled = true; };
  }, [ws, current]);
  const artifact = current?.artifact.ws_id === ws?.ws_id ? current?.artifact : null;
  async function action(kind: "reset" | "stop" | "check") {
    if (!ws || !artifact || busy) return;
    setBusy(true); setError(null);
    try {
      const path = `/ws/${ws.ws_id}/artifacts/${artifact.artifact_id}/${kind}`;
      if (kind === "check") {
        const choice = specs.lab.checks.find((item) => item.id === checkId)!;
        const { body } = await post<Check>(path, { check_id: checkId, params: choice.params });
        publish({ artifact, result: body });
      } else {
        const { body } = await post<Artifact>(path);
        publish({ artifact: body, result: null });
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  return <section style={{ padding: "6px 12px", height: "100%", display: "flex", flexDirection: "column" }} aria-label="Lab terminal">
    <div className="row"><strong>Lab terminal</strong> <span role="status">{artifact?.status ?? "not started"}</span>
      {artifact?.status === "running" && <><button disabled={busy} onClick={() => action("reset")}>Reset</button><button disabled={busy} onClick={() => action("stop")}>Stop</button>
        <label>Check <select value={checkId} onChange={(event) => setCheckId(event.target.value)}>{specs.lab.checks.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>
        <button disabled={busy} onClick={() => action("check")}>Run check</button></>}
    </div>
    {error && <p role="alert">{error}</p>}
    {current?.result && artifact?.artifact_id === current.result.artifact_id && <div role="status"><strong>{current.result.check_id}: {current.result.passed ? "passed" : "failed"}</strong><pre>{JSON.stringify(current.result.observed, null, 2)}</pre></div>}
    {artifact?.status === "running" && artifact.lab && <TerminalPane
      key={artifact.lab.lab_instance_id}
      lab={{ lab_instance_id: artifact.lab.lab_instance_id, attempt_id: "", status: "ready", terminal_id: null,
        terminal_path: `/v2/artifacts/${artifact.artifact_id}/terminal`, replaced_by_lab_instance_id: null }}
      storedOutput={[]}
      onLabStatus={() => {}}
      onServerMessage={(message) => { if (message.type === "error") setError(message.payload.message); }}
      handleRef={terminalHandle}
    />}
  </section>;
}
