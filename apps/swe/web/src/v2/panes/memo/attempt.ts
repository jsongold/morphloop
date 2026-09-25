export type Attempt = { wsId: string; body: string; key: string };

export function attemptFor(previous: Attempt | null, wsId: string, body: string, newKey: () => string): Attempt {
  return previous?.wsId === wsId && previous.body === body ? previous : { wsId, body, key: newKey() };
}
