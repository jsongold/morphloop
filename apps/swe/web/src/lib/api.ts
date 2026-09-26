// The backend base URL and the terminal WebSocket URL built from it. The HTTP
// client of the UI is `src/v2/api.ts`.

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function terminalSocketUrl(terminalPath: string): string {
  const u = new URL(terminalPath, API_BASE_URL);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}
