export type Attempt = { path: string; text: string; key: string };
export type Message = { message_id: string; role: "learner" | "assistant"; text: string; created_at: string };

export function attemptFor(previous: Attempt | null, path: string, text: string, newKey: () => string): Attempt {
  return previous?.path === path && previous.text === text ? previous : { path, text, key: newKey() };
}

export function keyForWorkspace(keys: Map<string, string>, wsId: string, newKey: () => string): string {
  let key = keys.get(wsId);
  if (!key) {
    key = newKey();
    keys.set(wsId, key);
  }
  return key;
}

export function mergeMessages(current: Message[], added: Message[]): Message[] {
  const seen = new Set(current.map((message) => message.message_id));
  return [...current, ...added.filter((message) => !seen.has(message.message_id))];
}

export const clearIfUnchanged = (current: string, submitted: string): string =>
  current === submitted ? "" : current;

// Sending is gated on a settled history load for the active thread. A send
// bumps the load version to make the follow-up refresh authoritative, so if it
// races the pending initial GET that GET is discarded; with no older messages
// in hand, a failed refresh would leave the log showing only the new exchange.
export function canSend(
  loadedPath: string | null,
  path: string | null,
  text: string,
  sending: boolean,
): boolean {
  return !!path && loadedPath === path && text.trim().length > 0 && !sending;
}

// A load can be retried once it has failed for the active path: a success
// sets loadedPath to path, a pending load has no error yet, and a send error
// (loadedPath === path) is handled by resending instead.
export function canRetryLoad(loadedPath: string | null, path: string | null, error: string | null): boolean {
  return !!path && loadedPath !== path && !!error;
}

// Whether the active thread carries the hint-mode label. `labelsOf` maps
// thread_id -> labels for threads this pane has itself created (currently
// just the main thread); a thread selected by id that we haven't created
// ourselves (e.g. a targeted thread another pane created) has no entry, so
// this reports false rather than guessing. TODO(#129): once GET
// /v2/ws/{ws_id}/threads exists, look up an unseen activeId's labels there.
export function isHintThread(labelsOf: Map<string, string[] | undefined>, activeId: string | null): boolean {
  return !!activeId && !!labelsOf.get(activeId)?.includes("mode:hint");
}
