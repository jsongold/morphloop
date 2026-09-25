// Hand-written HTTP client for the v0.2 API (ADR-0017/0018). Same shape as
// `src/lib/api.ts` (v0.1) but every path is joined onto `/v2`
// (contracts/openapi/v0.2/root.yaml `servers`). Kept separate so v0.1 and
// v0.2 migrate independently.

import type { Problem } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

/**
 * One key per logical learner action (a UUID, the event id on the server).
 * Generate it once, keep it, and resend the same key on retry: the server then
 * returns the stored result instead of acting twice.
 */
export const newIdempotencyKey = (): string => crypto.randomUUID();

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
  method: "GET" | "POST" | "DELETE",
  path: string,
  opts: { query?: Query; body?: unknown; key?: string } = {},
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.key !== undefined) headers["Idempotency-Key"] = opts.key;
  const res = await fetch(url(path, opts.query), {
    method,
    headers,
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
  return { body: json as T, replayed: res.headers.get("Idempotent-Replayed") === "true" };
}

/** GET a `/v2`-relative path, e.g. `get<Session>("/sessions/ses_abc")`. */
export const get = async <T>(path: string, query?: Query): Promise<T> =>
  (await request<T>("GET", path, { query })).body;

/**
 * POST a `/v2`-relative path with an `Idempotency-Key`. Pass the key you
 * generated for this action when retrying; omit it for a fresh action.
 */
export const post = <T>(path: string, body?: unknown, key = newIdempotencyKey()): Promise<ApiResult<T>> =>
  request<T>("POST", path, { body, key });

/** DELETE a `/v2`-relative path (e.g. remove a highlight); same key rule as `post`. */
export const del = (path: string, key = newIdempotencyKey()): Promise<ApiResult<null>> =>
  request<null>("DELETE", path, { key });
