// Hand-written HTTP client for the harness backend API.
//
// Per ADR-0015/0017: the standard UI couples to the backend only via the
// HTTP/WebSocket contract. Types here are hand-written, not generated.

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/health`);

  if (!res.ok) {
    throw new Error(`GET /health failed with status ${res.status}`);
  }

  return (await res.json()) as HealthResponse;
}
