export interface DrillItem {
  id: string;
  question: string;
  answer_mode: "text" | "choice" | "artifact";
  choices?: string[];
  artifact_ref?: string;
  labels: string[];
}

export interface DrillAnswer {
  item_id: string;
  answer_event_id: string;
  judgment_status: "unjudged" | "judged";
  gap: unknown;
}

export interface Artifact {
  artifact_id: string;
  type: string;
  spec_id: string;
  status: "running" | "stopped";
}

export interface Submission {
  wsId: string;
  itemId: string;
  body: { actual: string } | { artifact_id: string };
  key: string;
}

export function drillPath(labels: string[]): string {
  const query = new URLSearchParams();
  for (const label of labels) query.append("labels", label);
  return `/drills${query.size ? `?${query}` : ""}`;
}

export function answersPath(wsId: string): string {
  return `/ws/${encodeURIComponent(wsId)}/drills/answers`;
}

export const artifactsPath = (wsId: string): string => `/ws/${encodeURIComponent(wsId)}/artifacts`;

/** item_id -> answer_event_id, so a reload shows already-answered items. */
export function answeredItems(answers: DrillAnswer[]): Record<string, string> {
  return Object.fromEntries(answers.map((answer) => [answer.item_id, answer.answer_event_id]));
}

/** Distinct artifact specs the artifact-mode items ask for, in stable order. */
export function drillArtifactRefs(items: DrillItem[]): string[] {
  return [...new Set(items.flatMap((item) => (item.answer_mode === "artifact" && item.artifact_ref ? [item.artifact_ref] : [])))].sort();
}

/** The learner's artifacts whose spec is the item's artifact_ref (empty -> start the lab first). */
export function artifactOptions(artifacts: Artifact[], artifactRef: string | undefined): Artifact[] {
  return artifactRef ? artifacts.filter((artifact) => artifact.spec_id === artifactRef) : [];
}

// Submission waits for the active workspace's answer history to load. With no
// history in hand an already-answered item looks answerable and a failed load
// hides the stored answer, so an early POST either double-answers the item or
// gets overwritten when the older GET later replaces the state.
export function canSubmit(loadedWsId: string | null, wsId: string | null, value: string, saving: boolean): boolean {
  return !!wsId && loadedWsId === wsId && value.trim().length > 0 && !saving;
}

export function answerRequest(wsId: string, item: DrillItem, value: string, previous: Submission | null, newKey: () => string): Submission | null {
  const actual = item.answer_mode === "text" ? value.trim() : value;
  if (!actual || (item.answer_mode === "choice" && !item.choices?.includes(actual))) return null;
  if (item.answer_mode === "artifact" && !/^art_[0-9A-Za-z]{1,64}$/.test(actual)) return null;
  const body = item.answer_mode === "artifact" ? { artifact_id: actual } : { actual };
  const key = previous?.wsId === wsId && previous.itemId === item.id && JSON.stringify(previous.body) === JSON.stringify(body)
    ? previous.key : newKey();
  return { wsId, itemId: item.id, body, key };
}
