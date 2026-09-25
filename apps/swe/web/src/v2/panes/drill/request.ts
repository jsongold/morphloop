export interface DrillItem {
  id: string;
  question: string;
  answer_mode: "text" | "choice" | "artifact";
  choices?: string[];
  artifact_ref?: string;
  labels: string[];
}

export interface Submission {
  wsId: string;
  itemId: string;
  actual: string;
  key: string;
}

export function drillPath(labels: string[]): string {
  const query = new URLSearchParams();
  for (const label of labels) query.append("labels", label);
  return `/drills${query.size ? `?${query}` : ""}`;
}

export function answerRequest(wsId: string, item: DrillItem, value: string, previous: Submission | null, newKey: () => string): Submission | null {
  const actual = item.answer_mode === "text" ? value.trim() : value;
  if (!actual || item.answer_mode === "artifact" || (item.answer_mode === "choice" && !item.choices?.includes(actual))) return null;
  const key = previous?.wsId === wsId && previous.itemId === item.id && previous.actual === actual
    ? previous.key : newKey();
  return { wsId, itemId: item.id, actual, key };
}
