"use client";

import type { StoredEvent } from "@/lib/types";

/** Session timeline in `position` order (AC-F2, AC-F7). Consecutive output chunks are grouped. */
export function TimelineView({ events }: { events: StoredEvent[] }) {
  const rows: { first: StoredEvent; count: number; text: string }[] = [];
  for (const e of events) {
    const last = rows[rows.length - 1];
    if (e.event_type === "terminal.output" && last?.first.event_type === "terminal.output") {
      last.count += 1;
      last.text += outputText(e);
      continue;
    }
    rows.push({ first: e, count: 1, text: e.event_type === "terminal.output" ? outputText(e) : "" });
  }
  return (
    <ol className="timeline">
      {rows.map(({ first: e, count, text }) => (
        <li key={e.event_id}>
          <span className="muted">#{e.position}</span> <code>{e.event_type}</code>{" "}
          <span className="muted">{e.actor}</span>{" "}
          {e.event_type === "terminal.output" ? (
            <details>
              <summary>
                {count} output chunk{count > 1 ? "s" : ""}
              </summary>
              <pre>{text}</pre>
            </details>
          ) : (
            <span>{summarize(e)}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

function outputText(e: StoredEvent): string {
  const p = e.payload as { data?: string; encoding?: string };
  if (typeof p.data !== "string") return "";
  if (p.encoding !== "base64") return p.data;
  try {
    const bin = atob(p.data);
    return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
  } catch {
    return "";
  }
}

function summarize(e: StoredEvent): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.event_type) {
    case "terminal.command":
      return `$ ${String(p.command ?? "")}`;
    case "assistant.message_requested":
    case "assistant.message_generated":
      return truncate(String(p.text ?? ""));
    case "content.highlighted":
      return `“${truncate(String(p.selected_text ?? ""))}”`;
    case "content.opened":
      return String(p.content_id ?? "");
    case "visualization.step_selected":
      return `${String(p.visualization_id ?? "")} → ${String(p.step_id ?? "")}`;
    case "activity.completed":
      return String(p.outcome ?? "");
    case "evaluation.completed":
      return p.success ? "success" : "not yet";
    case "evidence.created":
      return `${String(p.skill_id ?? "")} ${String(p.signal ?? "")}`;
    case "learner_skill.updated":
      return `${String(p.skill_id ?? "")}`;
    case "lab.started":
      return String(p.trigger ?? "");
    default:
      return "";
  }
}

function truncate(s: string, n = 80): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
