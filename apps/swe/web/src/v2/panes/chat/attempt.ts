export type Attempt = { path: string; text: string; key: string };

export function attemptFor(previous: Attempt | null, path: string, text: string, newKey: () => string): Attempt {
  return previous?.path === path && previous.text === text ? previous : { path, text, key: newKey() };
}
