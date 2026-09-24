// Hand-written HTTP client for the v0.2 resource-centric API (ADR-0018).
//
// Same shape as `src/lib/api.ts` for v0.1: no code generation (ADR-0017), no
// harness internals imported. Every request goes to the `/v2` base path
// (contracts/openapi/v0.2/root.yaml `servers`). Kept separate from
// `src/lib/api.ts` so v0.1 and v0.2 clients migrate independently
// (issue #34).

import type { Problem } from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const V2_BASE_URL = `${API_BASE_URL}/v2`;

/** Non-2xx response. `problem` is set when the body was a Problem document. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiResult<T> {
  body: T;
  /** `Idempotent-Replayed: true` was returned (ADR-0008). */
  replayed: boolean;
}

type Query = Record<string, string | number | undefined>;

/** `path` must start with "/", e.g. "/sessions/ses_abc" (joined onto `/v2` directly). */
function url(path: string, query?: Query): string {
  const u = new URL(`${V2_BASE_URL}${path}`);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined) u.searchParams.set(k, String(v));
  }
  return u.toString();
}

function isProblem(v: unknown): v is Problem {
  return typeof v === "object" && v !== null && "code" in v && "status" in v;
}

async function request<T>(
  method: "GET" | "POST",
  path: string,
  opts: { query?: Query; body?: unknown } = {},
): Promise<ApiResult<T>> {
  const res = await fetch(url(path, opts.query), {
    method,
    headers:
      opts.body === undefined
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const text = await res.text();
  const json: unknown = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const problem = isProblem(json) ? json : null;
    throw new ApiError(
      res.status,
      problem,
      problem
        ? `${problem.title}${problem.detail ? `: ${problem.detail}` : ""}`
        : `${method} ${path} failed with status ${res.status}`,
    );
  }
  return {
    body: json as T,
    replayed: res.headers.get("Idempotent-Replayed") === "true",
  };
}

/** GET a `/v2`-relative path, e.g. `get<Session>("/sessions/ses_abc")`. */
export const get = async <T>(path: string, query?: Query): Promise<T> =>
  (await request<T>("GET", path, { query })).body;

/** POST a `/v2`-relative path. Returns the full result (`replayed` included). */
export const post = <T>(path: string, body?: unknown): Promise<ApiResult<T>> =>
  request<T>("POST", path, { body });
