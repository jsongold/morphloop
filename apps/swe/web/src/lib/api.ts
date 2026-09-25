// Hand-written HTTP client for the harness backend API.
//
// Per ADR-0015/0017: the standard UI couples to the backend only via the
// HTTP/WebSocket contract (contracts/openapi/v0.1.yaml). Types here are
// hand-written, not generated, and no harness internals are imported.

import type {
  ActivityView,
  AttemptState,
  ChatEvent,
  ChatExchange,
  ChatMessageRequest,
  ClientEventRequest,
  ContentDocument,
  ContentKind,
  ContentSummary,
  HighlightEvent,
  Health,
  LabState,
  Learner,
  MemoView,
  Pack,
  Problem,
  Session,
  SessionState,
  SkillStateList,
  StoredEvent,
  TimelinePage,
} from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
  /** `Idempotent-Replayed: true` was returned (ADR-0008, AC-F5). */
  replayed: boolean;
}

type Query = Record<string, string | number | undefined>;

function url(path: string, query?: Query): string {
  const u = new URL(path, API_BASE_URL);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined) u.searchParams.set(k, String(v));
  }
  return u.toString();
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

function isProblem(v: unknown): v is Problem {
  return typeof v === "object" && v !== null && "code" in v && "status" in v;
}

const get = async <T>(path: string, query?: Query) =>
  (await request<T>("GET", path, { query })).body;
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, { body });

const seg = encodeURIComponent;

// ---------- system / packs / learners ----------

export const getHealth = () => get<Health>("/health");

export const listPacks = () => get<{ packs: Pack[] }>("/packs");

export const createLearner = async () => (await post<Learner>("/learners")).body;

export const listLearnerSessions = (learnerId: string) =>
  get<{ sessions: Session[] }>(`/learners/${seg(learnerId)}/sessions`);

export const getLearnerSkills = (learnerId: string, packId?: string) =>
  get<SkillStateList>(`/learners/${seg(learnerId)}/skills`, { pack_id: packId });

// ---------- sessions / content ----------

export const startSession = (body: {
  idempotency_key: string;
  learner_id: string;
  pack_id: string;
  pack_content_hash: string;
}) => post<SessionState>("/sessions", body);

export const getSession = (sessionId: string) =>
  get<SessionState>(`/sessions/${seg(sessionId)}`);

/** The pack layout document, opaque in v0.1 (ADR-0012), or null. */
export const getSessionLayout = (sessionId: string) =>
  get<{ layout: Record<string, unknown> | null }>(`/sessions/${seg(sessionId)}/layout`);

export const listSessionActivities = (sessionId: string) =>
  get<{ activities: ActivityView[] }>(`/sessions/${seg(sessionId)}/activities`);

export const listSessionContent = (sessionId: string, kind?: ContentKind) =>
  get<{ items: ContentSummary[] }>(`/sessions/${seg(sessionId)}/content`, { kind });

export const getSessionContent = <D = Record<string, unknown>>(
  sessionId: string,
  kind: ContentKind,
  definitionId: string,
) =>
  get<ContentDocument<D>>(
    `/sessions/${seg(sessionId)}/content/${seg(kind)}/${seg(definitionId)}`,
  );

// ---------- attempts / labs ----------

export const startAttempt = (
  sessionId: string,
  body: { idempotency_key: string; activity_definition_id: string },
) => post<AttemptState>(`/sessions/${seg(sessionId)}/attempts`, body);

export const getAttempt = (attemptId: string) =>
  get<AttemptState>(`/attempts/${seg(attemptId)}`);

export const submitAttempt = (attemptId: string, idempotencyKey: string) =>
  post<AttemptState>(`/attempts/${seg(attemptId)}/submit`, {
    idempotency_key: idempotencyKey,
  });

export const getLab = (labInstanceId: string) =>
  get<LabState>(`/labs/${seg(labInstanceId)}`);

/** Returns the replacement lab (in `starting`); reconnect to its terminal_path. */
export const resetLab = (labInstanceId: string, idempotencyKey: string) =>
  post<LabState>(`/labs/${seg(labInstanceId)}/reset`, {
    idempotency_key: idempotencyKey,
  });

// ---------- events / chat / timeline ----------

export const appendClientEvent = (sessionId: string, body: ClientEventRequest) =>
  post<{ event: StoredEvent }>(`/sessions/${seg(sessionId)}/events`, body);

export const sendChatMessage = (sessionId: string, body: ChatMessageRequest) =>
  post<ChatExchange>(`/sessions/${seg(sessionId)}/chat/messages`, body);

export const getSessionChat = (sessionId: string, threadId?: string) =>
  get<{ events: ChatEvent[] }>(`/sessions/${seg(sessionId)}/chat`, { thread_id: threadId });

export const getSessionHighlights = (sessionId: string) =>
  get<{ events: HighlightEvent[] }>(`/sessions/${seg(sessionId)}/highlights`);

export const getSessionMemos = (sessionId: string) =>
  get<{ memos: MemoView[] }>(`/sessions/${seg(sessionId)}/memos`);

export const getSessionTimeline = (
  sessionId: string,
  params: { after_position?: number; limit?: number } = {},
) => get<TimelinePage>(`/sessions/${seg(sessionId)}/timeline`, params);

/** Pages forward from `afterPosition` until `has_more` is false. */
export async function getTimelineSince(
  sessionId: string,
  afterPosition = 0,
): Promise<TimelinePage> {
  const events: StoredEvent[] = [];
  let cursor = afterPosition;
  for (;;) {
    const page = await getSessionTimeline(sessionId, {
      after_position: cursor,
      limit: 500,
    });
    events.push(...page.events);
    cursor = page.last_position;
    if (!page.has_more) return { events, last_position: cursor, has_more: false };
  }
}

/** ws(s):// URL of a lab's terminal WebSocket from its `terminal_path`. */
export function terminalSocketUrl(terminalPath: string): string {
  const u = new URL(terminalPath, API_BASE_URL);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}
