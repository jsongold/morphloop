// Hand-written wire types for the v0.2 API, only the shapes the /v2 shell
// needs (ADR-0017: no codegen). Source of truth: contracts/openapi/v0.2/.
// A pane adds the types it needs under its own src/v2/panes/<name>/ dir.

// ---------- errors (contracts/openapi/v0.2/components/common.yaml#Problem) ----------

export type ProblemCode =
  | "invalid-request"
  | "not-found"
  | "idempotency-key-reused"
  | "state-conflict"
  | "validation-failed"
  | "llm-failed"
  | "lab-unavailable"
  | "internal";

export interface Problem {
  type: string;
  title: string;
  status: number;
  code: ProblemCode;
  detail?: string;
  instance?: string;
  errors?: { path: string; message: string }[];
}

// ---------- events (contracts/schemas/events/envelope/v2/stored.json) ----------

export interface StoredEvent<P = Record<string, unknown>> {
  id: string;
  position: number;
  type: string; // "<resource>.<past tense>", e.g. "ws.created"
  created_at: string;
  actor: "learner" | "assistant" | "system";
  user_id: string;
  session_id?: string;
  ws_id?: string;
  payload: P;
}

// ---------- session (paths/session.yaml; tree: contracts/schemas/pack/v2/topic.json) ----------

export interface TopicNode {
  id: string;
  title: string;
  description?: string;
  /** Reading order: textbook doc ids (`GET /textbook/docs?topic_id=`). */
  docs?: string[];
  topics?: TopicNode[];
}

export interface Session {
  id: string; // ses_*
  user_id: string;
  pack_id: string;
  pack_version: string;
  pack_content_hash: string;
  topic_id: string;
  tree: TopicNode;
  created_at: string;
}

// ---------- ws (paths/ws.yaml) ----------

export interface Ws {
  ws_id: string;
  user_id: string;
  session_id: string;
  labels: string[];
  created_at: string;
  position: number;
  main_thread_event_id: string | null;
}
