"use client";

// The artifact pane (#115): the inline slot starts/reuses a lab and draws a
// diagram; the bottom pane shows the active lab's terminal, status, reset/stop
// and the named check's result.
//
// A pane only touches its own directory (panes/types.ts). The workbook's
// `::artifact{type=... ref=...}` directive renders <ArtifactSlot/> inline; the
// module registry below maps a type to its renderer. The bottom slot mounts
// <ArtifactPane/> independently, so what the inline slot starts is published to
// a tiny module store both read.

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import TerminalPane, { type TerminalHandle } from "@/components/TerminalPane";
import { get, post } from "../../api";
import { useWorkspace } from "../../state";
import type { ArtifactDirective } from "../types";
import {
  artifactSpec,
  checkRequest,
  labAction,
  openArtifact,
  runCheck,
  startLab,
  type Artifact,
  type CheckResult,
} from "./artifacts";

export type ArtifactRenderer = (directive: ArtifactDirective) => ReactNode;
const renderers = new Map<string, ArtifactRenderer>();
export function registerArtifactRenderer(type: string, renderer: ArtifactRenderer): void { renderers.set(type, renderer); }
export function ArtifactSlot({ directive }: { directive: ArtifactDirective }) {
  return renderers.get(directive.type)?.(directive) ?? <p className="muted">Unsupported artifact: {directive.type}</p>;
}

// One active lab per page is shown; the inline slot publishes what it starts or
// reuses and the bottom pane renders it. ponytail: per-artifact state if a pack
// embeds several labs at once.
type Active = { artifact: Artifact; result: CheckResult | null } | null;
let active: Active = null;
const listeners = new Set<() => void>();
function publish(next: Active) { active = next; listeners.forEach((listener) => listener()); }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function useActive(): Active { return useSyncExternalStore(subscribe, () => active, () => null); }

function LabView({ directive }: { directive: ArtifactDirective }) {
  const { ws } = useWorkspace();
  const active = useActive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsId = ws?.ws_id ?? null;
  const running =
    active?.artifact.ws_id === wsId &&
    active.artifact.spec_id === directive.ref &&
    active.artifact.status === "running";

  // Reuse a lab already started for this directive instead of starting another.
  useEffect(() => {
    if (!wsId || running) return;
    let cancelled = false;
    openArtifact(get, wsId, directive.ref).then(
      (artifact) => { if (!cancelled && artifact) publish({ artifact, result: null }); },
      (cause: unknown) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); },
    );
    return () => { cancelled = true; };
  }, [wsId, directive.ref, running]);

  async function start() {
    if (!wsId || busy) return;
    setBusy(true);
    setError(null);
    try {
      publish({ artifact: await startLab(post, wsId, directive.ref), result: null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="row">
      <strong>Lab</strong>{" "}
      <span role="status">{running ? active?.artifact.status : "not started"}</span>
      <button onClick={() => void start()} disabled={!wsId || busy || running}>Start lab</button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}

type Observation = { argv: string[]; purpose: string; look_for?: string };
type Observable = { kind: string; locator: string; description: string };
type DiagramStep = {
  id: string;
  from: string;
  to: string;
  label: string;
  explanation: string;
  reality?: { mechanism: string; observe: Observation[]; artifacts?: Observable[] };
};
type Diagram = {
  title: string;
  diagram: { actors: { id: string; label: string }[]; steps: DiagramStep[] };
};

// The diagram's styles must live in the shadow tree: ArtifactHost portals this
// pane into a ShadowRoot, so the global .viz-* rules in globals.css never reach
// it. Without a stroke the SVG lifelines and arrows default to none and vanish.
const diagramStyle = `
.viz-svg { width: 100%; max-height: 320px; color: var(--foreground); }
.viz-svg text { fill: currentColor; font-size: 12px; }
.viz-actor { font-weight: 600; }
.viz-lifeline { stroke: var(--border); stroke-dasharray: 4 3; }
.viz-step line, .viz-step path { stroke: currentColor; stroke-width: 1.5; }
.viz-step { cursor: pointer; }
.viz-steps { margin: 6px 0; }
.viz-detail { border-top: 1px solid var(--border); padding-top: 6px; }
`;

function DiagramView({ directive }: { directive: ArtifactDirective }) {
  const [spec, setSpec] = useState<ArtifactSpecView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    artifactSpec(get, directive.ref).then(
      (loaded) => { if (!cancelled) setSpec({ type: loaded.type, spec: loaded.spec as unknown as Diagram }); },
      (cause: unknown) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); },
    );
    return () => { cancelled = true; };
  }, [directive.ref]);
  if (error) return <p role="alert">{error}</p>;
  if (!spec) return <p className="muted">Loading diagram…</p>;
  if (spec.type !== "diagram") return <p role="alert">Artifact {directive.ref} is not a diagram.</p>;
  return <DiagramContent spec={spec.spec} />;
}

export function DiagramContent({ spec }: { spec: Diagram }) {
  const [selected, setSelected] = useState(0);
  const { title, diagram } = spec;
  const x = (actorId: string) => (diagram.actors.findIndex((actor) => actor.id === actorId) + 0.5) * 190;
  const step = diagram.steps[selected];
  return <section className="viz"><style>{diagramStyle}</style><h3>{title}</h3>
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
      {step.reality && <><h5>Real mechanism</h5><p>{step.reality.mechanism}</p><h5>Observe it</h5><ul>{step.reality.observe.map((item, i) => <li key={i}><code>{item.argv.join(" ")}</code> — {item.purpose}{item.look_for && <p>Look for: {item.look_for}</p>}</li>)}</ul>
        {step.reality.artifacts && <><h5>Observable state</h5><ul>{step.reality.artifacts.map((item, i) => <li key={i}><code>{item.locator}</code> ({item.kind}) — {item.description}</li>)}</ul></>}
      </>}
    </div>
  </section>;
}

type ArtifactSpecView = { type: string; spec: Diagram };

registerArtifactRenderer("lab", (directive) => <LabView directive={directive} />);
registerArtifactRenderer("diagram", (directive) => <DiagramView directive={directive} />);

export default function ArtifactPane() {
  const { ws } = useWorkspace();
  const active = useActive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkId, setCheckId] = useState("");
  const [params, setParams] = useState("{}");
  const terminalHandle = useRef<TerminalHandle | null>(null);
  const wsId = ws?.ws_id ?? null;
  const artifact = active?.artifact.ws_id === wsId ? active.artifact : null;

  // On reload the inline slot may not have rendered yet; restore the ws's own
  // running lab so its terminal and controls are shown.
  useEffect(() => {
    if (!wsId || artifact) return;
    let cancelled = false;
    openArtifact(get, wsId).then(
      (found) => { if (!cancelled && found) publish({ artifact: found, result: null }); },
      () => {},
    );
    return () => { cancelled = true; };
  }, [wsId, artifact]);

  async function action(kind: "reset" | "stop") {
    if (!wsId || !artifact || busy) return;
    setBusy(true);
    setError(null);
    try {
      publish({ artifact: await labAction(post, wsId, artifact.artifact_id, kind), result: null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    if (!wsId || !artifact || busy) return;
    const request = checkRequest(checkId, params);
    if (!request.body) {
      setError(request.error ?? "Invalid check.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      publish({ artifact, result: await runCheck(post, wsId, artifact.artifact_id, request.body) });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return <section style={{ padding: "6px 12px", height: "100%", display: "flex", flexDirection: "column" }} aria-label="Lab terminal">
    <div className="row"><strong>Lab terminal</strong> <span role="status">{artifact?.status ?? "not started"}</span>
      {artifact?.status === "running" && <>
        <button disabled={busy} onClick={() => void action("reset")}>Reset</button>
        <button disabled={busy} onClick={() => void action("stop")}>Stop</button>
        <label>Check <input value={checkId} onChange={(event) => setCheckId(event.target.value)} placeholder="check id" /></label>
        <label>Params <input value={params} onChange={(event) => setParams(event.target.value)} placeholder="{}" /></label>
        <button disabled={busy || !checkId.trim()} onClick={() => void check()}>Run check</button>
      </>}
    </div>
    {error && <p role="alert">{error}</p>}
    {active?.result && artifact?.artifact_id === active.result.artifact_id && <div role="status"><strong>{active.result.check_id}: {active.result.passed ? "passed" : "failed"}</strong><pre>{JSON.stringify(active.result.observed, null, 2)}</pre></div>}
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
