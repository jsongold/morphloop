"use client";

import { HighlightableText } from "./HighlightableText";
import type {
  ContentDocument,
  HighlightEvent,
  StoredEvent,
  TerminalCommandPayload,
  VisualizationDocument,
} from "@/lib/types";
import { argvToCommandLine } from "@/lib/ws";

interface Props {
  content: ContentDocument<VisualizationDocument>;
  selectedStepId: string | null;
  onSelectStep: (stepId: string) => void;
  /** Types the command into the lab terminal; null when no lab is running. */
  onRun: ((line: string) => void) | null;
  /** terminal.command events of this session, for linking steps to real runs. */
  commands: StoredEvent<"terminal.command", TerminalCommandPayload>[];
  highlights: HighlightEvent[];
}

const COL_W = 170;
const ROW_H = 44;
const TOP = 40;

/**
 * Renders a pack `sequence` visualization (contracts/schemas/pack/
 * visualization.json) as an SVG sequence diagram with a keyboard-operable
 * step list as its text equivalent (AC-C1). Selecting a step exposes its
 * reality mapping: mechanism, runnable observations and artifacts (AC-C2),
 * and runs an observation in the lab terminal without leaving the activity
 * (AC-C3). Previous runs of the same command are linked from the timeline.
 */
export function VisualizationView({
  content,
  selectedStepId,
  onSelectStep,
  onRun,
  commands,
  highlights,
}: Props) {
  const doc = content.document;
  const actors = doc.diagram?.actors ?? [];
  const steps = doc.diagram?.steps ?? [];
  if (doc.diagram?.type !== "sequence" || actors.length === 0) {
    return <p className="muted">Unsupported visualization type.</p>;
  }
  const col = new Map(actors.map((a, i) => [a.id, i]));
  const selIndex = steps.findIndex((s) => s.id === selectedStepId);
  const selected = selIndex >= 0 ? steps[selIndex] : null;
  const width = actors.length * COL_W;
  const height = TOP + steps.length * ROW_H + 20;
  const x = (id: string) => (col.get(id) ?? 0) * COL_W + COL_W / 2;
  const version = content.content_version;

  return (
    <div className="viz">
      <div className="viz-header">
        <strong>{doc.title}</strong>
        <div className="row">
          <button
            disabled={selIndex <= 0}
            onClick={() => onSelectStep(steps[selIndex - 1].id)}
            aria-label="Previous step"
          >
            ◀ Prev
          </button>
          <span className="muted">
            {selIndex >= 0 ? `step ${selIndex + 1} / ${steps.length}` : `${steps.length} steps`}
          </span>
          <button
            disabled={selIndex >= steps.length - 1}
            onClick={() => onSelectStep(steps[selIndex + 1].id)}
            aria-label="Next step"
          >
            Next ▶
          </button>
        </div>
      </div>

      <svg
        className="viz-svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Sequence diagram: ${doc.title}`}
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>
        {actors.map((a) => (
          <g key={a.id}>
            <text x={x(a.id)} y={18} textAnchor="middle" className="viz-actor">
              {a.label}
            </text>
            <line x1={x(a.id)} y1={26} x2={x(a.id)} y2={height - 6} className="viz-lifeline" />
          </g>
        ))}
        {steps.map((s, i) => {
          const y = TOP + i * ROW_H + ROW_H / 2;
          const x1 = x(s.from);
          const x2 = x(s.to);
          const isSel = s.id === selectedStepId;
          const self = x1 === x2;
          return (
            <g
              key={s.id}
              className={`viz-step${isSel ? " selected" : ""}${s.reality ? " has-reality" : ""}`}
              onClick={() => onSelectStep(s.id)}
            >
              <rect x={0} y={y - ROW_H / 2} width={width} height={ROW_H} className="viz-hit" />
              {self ? (
                <path d={`M${x1},${y - 6} h30 v12 h-30`} fill="none" markerEnd="url(#arrow)" />
              ) : (
                <line x1={x1} y1={y + 6} x2={x2} y2={y + 6} markerEnd="url(#arrow)" />
              )}
              <text x={(x1 + x2) / 2 + (self ? 40 : 0)} y={y} textAnchor={self ? "start" : "middle"}>
                {i + 1}. {s.label}
                {s.reality ? " ⚙" : ""}
              </text>
            </g>
          );
        })}
      </svg>

      <ol className="viz-steps" aria-label="Steps">
        {steps.map((s) => (
          <li key={s.id}>
            <button
              className={s.id === selectedStepId ? "link selected" : "link"}
              aria-pressed={s.id === selectedStepId}
              onClick={() => onSelectStep(s.id)}
            >
              {actors.find((a) => a.id === s.from)?.label} → {actors.find((a) => a.id === s.to)?.label}: {s.label}
              {s.reality ? " (real mechanism)" : ""}
            </button>
          </li>
        ))}
      </ol>

      {selected && (
        <section className="viz-detail" aria-live="polite">
          <h4>{selected.label}</h4>
          {selected.explanation && (
            <HighlightableText
              text={selected.explanation}
              contentId={doc.id}
              contentVersion={version}
              anchor={`step:${selected.id}:explanation`}
              highlights={highlights}
            />
          )}
          {selected.reality ? (
            <>
              <h5>Real mechanism</h5>
              <HighlightableText
                text={selected.reality.mechanism}
                contentId={doc.id}
                contentVersion={version}
                anchor={`step:${selected.id}:mechanism`}
                highlights={highlights}
              />
              <h5>Observe it</h5>
              <ul className="observe">
                {selected.reality.observe.map((o, i) => {
                  const line = argvToCommandLine(o.argv);
                  const runs = commands.filter((c) => c.payload.command.trim() === line);
                  return (
                    <li key={i}>
                      <code>{line}</code>{" "}
                      <button disabled={!onRun} onClick={() => onRun?.(line)}>
                        Run in terminal
                      </button>
                      <div>{o.purpose}</div>
                      {o.look_for && <div className="muted">Look for: {o.look_for}</div>}
                      {runs.length > 0 && (
                        <div className="muted">
                          You ran this {runs.length}× (timeline #{runs.map((r) => r.position).join(", #")})
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              {selected.reality.artifacts && selected.reality.artifacts.length > 0 && (
                <>
                  <h5>Observable state</h5>
                  <ul>
                    {selected.reality.artifacts.map((a, i) => (
                      <li key={i}>
                        <code>{a.locator}</code> <span className="muted">({a.kind})</span> — {a.description}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : (
            <p className="muted">This step has no reality mapping.</p>
          )}
        </section>
      )}
    </div>
  );
}
