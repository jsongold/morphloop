// Client-generated identifiers (contracts/schemas/common/ids.json).

const ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

function randomAlnum(length: number): string {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += ALNUM[b % ALNUM.length];
  return out;
}

/** Time-prefixed so keys sort roughly by creation; opaque to the server. */
function timeOrderedTail(): string {
  return Date.now().toString(36).toUpperCase().padStart(10, "0") + randomAlnum(16);
}

/** Namespaced idempotency key, e.g. `web:0LX...` (contracts/openapi/README.md). */
export function newIdempotencyKey(): string {
  return `web:${timeOrderedTail()}`;
}

/** Highlight ids are client-generated with the `hl_` prefix. */
export function newHighlightId(): string {
  return `hl_${timeOrderedTail()}`;
}

export function nowTimestamp(): string {
  // toISOString is always UTC with a `Z` suffix.
  return new Date().toISOString();
}
