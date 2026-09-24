// Hand-written wire types for the v0.2 resource-centric contract (ADR-0018).
//
// Source of truth: contracts/openapi/v0.2/, contracts/schemas/events/envelope/v2/,
// contracts/schemas/common/ids.json. No code generation (ADR-0017); kept in
// sync by hand as each resource PR adds its own paths/schemas. This file is
// deliberately separate from `src/lib/types.ts` (the v0.1 contract) so the
// v0.1 and v0.2 UIs stay independently migratable (parallel migration,
// issue #34).

// ---------- ids (contracts/schemas/common/ids.json) ----------

/** v0.2 event id: client-generated UUID, doubles as the idempotency key. */
export type EventUuid = string;
export type UserId = string; // usr_*
export type SessionId = string; // ses_*
export type WsId = string; // ws_*
export type Timestamp = string; // RFC 3339 UTC, Z suffix

// ---------- event envelope v2 (contracts/schemas/events/envelope/v2/) ----------

export type Actor = "learner" | "assistant" | "system";

/**
 * Envelope fields shared by every v0.2 event (fields.json). Not closed: the
 * payload shape is per event `type`, validated server-side by dispatch.json;
 * the UI treats `payload` as opaque unless a resource narrows it.
 */
export interface EventEnvelopeV2<P = Record<string, unknown>> {
  id: EventUuid;
  type: string; // '<resource>.<past tense>', e.g. 'ws.created'
  actor: Actor;
  user_id: UserId;
  session_id?: SessionId;
  ws_id?: WsId;
  payload: P;
}

/** What a producer sends to append an event (append.json): envelope fields only. */
export type AppendEventV2<P = Record<string, unknown>> = EventEnvelopeV2<P>;

/** An event as read back from the store (stored.json): envelope + DB-assigned fields. */
export interface StoredEventV2<P = Record<string, unknown>> extends EventEnvelopeV2<P> {
  position: number;
  created_at: Timestamp;
}

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
