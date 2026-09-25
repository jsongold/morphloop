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
  body: { actual: string } | { artifact_id: string };
  key: string;
}

export function drillPath(labels: string[]): string {
  const query = new URLSearchParams();
  for (const label of labels) query.append("labels", label);
  return `/drills${query.size ? `?${query}` : ""}`;
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
