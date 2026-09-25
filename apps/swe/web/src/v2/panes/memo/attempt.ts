export type Attempt = { wsId: string; body: string; key: string };
export type Entry = { entry_id: string; actor: "learner" | "assistant"; body: string; position: number };

export function attemptFor(previous: Attempt | null, wsId: string, body: string, newKey: () => string): Attempt {
  return previous?.wsId === wsId && previous.body === body ? previous : { wsId, body, key: newKey() };
}

export function mergeEntries(current: Entry[], added: Entry): Entry[] {
  return [...current.filter((entry) => entry.entry_id !== added.entry_id), added].sort((a, b) => a.position - b.position);
}

export const clearIfUnchanged = (current: string, submitted: string): string =>
  current === submitted ? "" : current;
