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

// Appending is gated on a settled entries load for the active workspace. An
// append bumps the load version to make the follow-up refresh authoritative,
// so if it races the pending initial GET that GET is discarded; with no older
// entries in hand, a failed refresh would leave the pane showing only the new
// note and hiding every stored one.
export function canAppend(
  loadedWsId: string | null,
  wsId: string | null,
  body: string,
  saving: boolean,
): boolean {
  return !!wsId && loadedWsId === wsId && body.trim().length > 0 && !saving;
}

// A load can be retried once it has failed for the active workspace: a success
// sets loadedWsId to wsId, a pending load has no error yet, and an append
// error (loadedWsId === wsId) is handled by resending instead.
export function canRetryLoad(loadedWsId: string | null, wsId: string | null, error: string | null): boolean {
  return !!wsId && loadedWsId !== wsId && !!error;
}
