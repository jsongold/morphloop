"use client";

import { HighlightableText } from "./HighlightableText";
import type { ActivityView, AttemptState, HighlightEvent } from "@/lib/types";

interface Props {
  attempt: AttemptState | null;
  activities: ActivityView[];
  /** content_version for activity text: the pack version (activities carry no own version). */
  contentVersion: string;
  highlights: HighlightEvent[];
  busy: boolean;
  onStart: (a: ActivityView) => void;
  onSubmit: () => void;
  onReset: () => void;
  onNext: () => void;
}

/** Current mission, attempt controls (reset, submit) and the evaluation result. */
export function MissionPanel({
  attempt,
  activities,
  contentVersion,
  highlights,
  busy,
  onStart,
  onSubmit,
  onReset,
  onNext,
}: Props) {
  if (!attempt) {
    return (
      <section className="mission">
        <h2>Choose an activity</h2>
        {activities.length === 0 && <p className="muted">No activities available.</p>}
        <ul className="activities">
          {activities.map((a) => (
            <li key={a.activity_definition_id}>
              <button disabled={busy} onClick={() => onStart(a)}>
                Start
              </button>{" "}
              <strong>{a.title}</strong> <span className="muted">{a.activity_type}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  const act = attempt.activity;
  const lab = attempt.lab;
  const texts = Object.entries(act.instructions).filter(
    (e): e is [string, string] => typeof e[1] === "string",
  );

  return (
    <section className="mission">
      <div className="mission-head">
        <h2>{act.title}</h2>
        <div className="row">
          <span className="badge">{attempt.status}</span>
          {lab && (
            <button
              disabled={busy || attempt.status !== "active" || lab.status === "resetting"}
              onClick={onReset}
              title="Reset the lab to its known starting state"
            >
              Reset lab
            </button>
          )}
          {attempt.status !== "completed" ? (
            <button disabled={busy || attempt.status !== "active"} onClick={onSubmit}>
              {attempt.status === "evaluating" ? "Evaluating…" : "Submit"}
            </button>
          ) : (
            <button onClick={onNext}>Choose next activity</button>
          )}
        </div>
      </div>
      {texts.map(([key, text]) => (
        <HighlightableText
          key={key}
          className="mission-text"
          text={text}
          contentId={act.activity_definition_id}
          contentVersion={contentVersion}
          anchor={`instructions.${key}`}
          highlights={highlights}
        />
      ))}
      {attempt.last_submission_error && (
        <p className="error">
          Last submission could not be evaluated: {attempt.last_submission_error.title}
          {attempt.last_submission_error.detail ? ` — ${attempt.last_submission_error.detail}` : ""}
        </p>
      )}
      {attempt.result && (
        <div className={`result ${attempt.result.outcome}`}>
          <strong>Result: {attempt.result.outcome}</strong>
          {attempt.result.evaluation.payload.rationale && <p>{attempt.result.evaluation.payload.rationale}</p>}
          <ul>
            {attempt.result.evaluation.payload.checks.map((c) => (
              <li key={c.check_id}>
                {c.passed ? "✓" : "✗"} <code>{c.check_id}</code>
              </li>
            ))}
          </ul>
          {attempt.result.evidence.map((e) => (
            <p key={e.event_id} className="muted">
              {e.payload.signal === "positive" ? "+" : "−"} {e.payload.skill_id} ({e.payload.dimension}):{" "}
              {e.payload.rationale}
            </p>
          ))}
          {attempt.result.skill_updates.map((u) => (
            <p key={u.event_id} className="muted">
              {u.payload.skill_id}: mastery {fmt(u.payload.previous?.mastery_probability)} →{" "}
              {fmt(u.payload.next.mastery_probability)}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function fmt(n: number | undefined): string {
  return n === undefined ? "—" : n.toFixed(2);
}
