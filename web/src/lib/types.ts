// Hand-written wire types for the v0.1 HTTP / WebSocket contract.
//
// Source of truth: contracts/openapi/v0.1.yaml, contracts/schemas/{common,
// events,ws,pack}/. No code generation (ADR-0017); keep in sync by hand.
// Only the shapes the standard UI reads are narrowed; everything else is kept
// open so unknown fields from the server never break parsing.

// ---------- common ----------

export type LearnerId = string; // usr_*
export type SessionId = string; // ses_*
export type AttemptId = string; // att_*
export type LabInstanceId = string; // lab_*
export type EventId = string; // evt_*
export type HighlightId = string; // hl_*
export type ThreadId = string; // thr_*
export type MessageId = string; // msg_*
export type TerminalId = string; // term_*
export type MemoId = string; // memo_*
export type DefinitionId = string;
export type SkillId = string;
export type ContentHash = string; // sha256:<hex>
export type Timestamp = string; // RFC 3339 UTC, Z suffix
export type VersionLabel = string;
export type Token = string;

export interface PackIdentity {
  pack_id: string;
  pack_version: VersionLabel;
  pack_content_hash: ContentHash;
}

export interface LlmProvenance {
  provider: Token;
  model: string;
  prompt_id: DefinitionId;
  prompt_version: VersionLabel;
  generation_parameters: Record<string, string | number | boolean | null>;
}

export interface SkillState {
  mastery_probability: number;
  uncertainty: number;
  retention_score?: number;
  transfer_score?: number;
  hint_dependency?: number;
  misconceptions?: Token[];
}

// ---------- errors ----------

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

// ---------- events ----------

export type Actor = "learner" | "tutor" | "system";

export interface StoredEvent<T extends string = string, P = Record<string, unknown>> {
  event_id: EventId;
  event_type: T;
  event_version: number;
  occurred_at: Timestamp;
  idempotency_key: string | null;
  causation_id: EventId | null;
  correlation_id: string | null;
  learner_id: LearnerId;
  session_id: SessionId;
  attempt_id: AttemptId | null;
  activity_definition_id: DefinitionId | null;
  actor: Actor;
  payload: P;
  position: number;
  recorded_at: Timestamp;
}

export type ChatReference =
  | { type: "highlight"; id: HighlightId }
  | { type: "event"; id: EventId };

/**
 * What text a highlight points at (contracts/schemas/events/payloads/
 * content.highlighted/2.json). Pack content and chat messages have UTF-16
 * offsets into the source text; terminal highlights are an inclusive
 * `sequence` range with no offsets.
 */
export type HighlightSource =
  | { kind: "pack"; content_id: DefinitionId; content_version: VersionLabel }
  | { kind: "chat_message"; message_id: MessageId }
  | { kind: "terminal"; terminal_id: TerminalId; first_sequence: number; last_sequence: number };

/** content_id of a pack source, or null for chat/terminal sources. */
export function packSourceContentId(s: HighlightSource): DefinitionId | null {
  return s.kind === "pack" ? s.content_id : null;
}

/** content_version of a pack source, or null for chat/terminal sources. */
export function packSourceContentVersion(s: HighlightSource): VersionLabel | null {
  return s.kind === "pack" ? s.content_version : null;
}

export interface ContentHighlightedPayload {
  highlight_id: HighlightId;
  source: HighlightSource;
  selected_text: string;
  /** UTF-16 offsets; null only for terminal sources. */
  start_offset: number | null;
  end_offset: number | null;
  semantic_anchor: string | null;
  context_before: string;
  context_after: string;
}

export interface ContentOpenedPayload {
  content_id: DefinitionId;
  content_version: VersionLabel;
  pane: Token;
  source_highlight_id: HighlightId | null;
}

export interface VisualizationStepSelectedPayload {
  visualization_id: DefinitionId;
  content_version: VersionLabel;
  step_id: string;
}

export interface MessageRequestedPayload {
  message_id: MessageId;
  thread_id: ThreadId;
  text: string;
  requested_mode?: Token | null;
  references: ChatReference[];
}

export interface MessageGeneratedPayload {
  message_id: MessageId;
  thread_id: ThreadId;
  in_reply_to: MessageId;
  text: string;
  mode: Token;
  references: ChatReference[];
  provenance: LlmProvenance;
}

export interface TerminalCommandPayload {
  terminal_id: TerminalId;
  lab_instance_id: LabInstanceId;
  sequence: number;
  command: string;
  cwd: string | null;
}

export interface TerminalOutputPayload {
  terminal_id: TerminalId;
  lab_instance_id: LabInstanceId;
  sequence: number;
  data: string;
  encoding: "utf-8" | "base64";
}

export interface EvaluationCompletedPayload {
  evaluation_id: string;
  evaluator: { definition_id: DefinitionId; definition_hash: ContentHash };
  lab_instance_id: LabInstanceId | null;
  checks: { check_id: string; passed: boolean; observed: Record<string, unknown> }[];
  success: boolean;
  rationale: string | null;
  provenance: LlmProvenance | null;
}

export interface EvidenceCreatedPayload {
  evidence_id: string;
  evaluation_id: string;
  skill_id: SkillId;
  signal: "positive" | "negative";
  strength: number;
  dimension: Token;
  rationale: string;
  supporting_event_ids: EventId[];
}

export interface LearnerSkillUpdatedPayload {
  pack_id: string;
  skill_id: SkillId;
  previous: SkillState | null;
  next: SkillState;
  evidence_ids: string[];
  rationale: string;
  predicted_success_probability: number;
  provenance: LlmProvenance;
}

export interface ActivityCompletedPayload {
  outcome: "passed" | "failed";
  evaluation_id: string;
}

export type HighlightEvent = StoredEvent<"content.highlighted", ContentHighlightedPayload>;
export type MessageRequestedEvent = StoredEvent<"assistant.message_requested", MessageRequestedPayload>;
export type MessageGeneratedEvent = StoredEvent<"assistant.message_generated", MessageGeneratedPayload>;
export type ChatEvent = MessageRequestedEvent | MessageGeneratedEvent;

/** Learner replacement of a memo's text (memo.edited/1.json). */
export interface MemoEditedPayload {
  memo_id: MemoId;
  title: string;
  body: string;
}

/** Current text of one learning memo (MemoView, openapi v0.1). */
export interface MemoView {
  memo_id: MemoId;
  highlight_id: HighlightId;
  thread_id: ThreadId;
  title: string;
  body: string;
  source_event_ids: EventId[];
  edited_by_learner: boolean;
  updated_at: Timestamp;
  last_event_id: EventId;
}

// ---------- HTTP-only shapes (components.schemas) ----------

export interface Health {
  status: "ok";
  db: "ok" | "down";
}

export interface Pack {
  pack: PackIdentity;
  title: string;
  imported_at: Timestamp;
}

export interface Learner {
  learner_id: LearnerId;
  created_at: Timestamp;
}

export interface SkillStateView {
  pack_id: string;
  skill_id: SkillId;
  state: SkillState;
  update_count: number;
  last_update_event_id: EventId;
  updated_at: Timestamp;
}

export interface SkillStateList {
  learner_id: LearnerId;
  skills: SkillStateView[];
}

export interface Session {
  session_id: SessionId;
  learner_id: LearnerId;
  pack: PackIdentity;
  started_at: Timestamp;
  session_started_event_id: EventId;
}

export interface UiState {
  open_content: ContentOpenedPayload | null;
  visualization_steps: VisualizationStepSelectedPayload[];
}

export interface SessionState {
  session: Session;
  last_position: number;
  active_attempt: AttemptState | null;
  ui_state: UiState;
}

export interface ActivityView {
  activity_definition_id: DefinitionId;
  activity_definition_hash: ContentHash;
  title: string;
  activity_type: Token;
  skill_ids: SkillId[];
  lab_backed: boolean;
  /** Verbatim pack `instructions` object; v0.1 pack schema: `{ mission: markdown }`. */
  instructions: Record<string, unknown>;
}

export type ContentKind = "skill" | "visualization" | "reference";

export interface ContentSummary {
  kind: ContentKind;
  definition_id: DefinitionId;
  definition_hash: ContentHash;
  content_version: VersionLabel;
  title: string;
}

export interface ContentDocument<D = Record<string, unknown>> extends ContentSummary {
  document: D;
}

export type LabStatus = "starting" | "ready" | "resetting" | "stopped" | "error";

export interface LabState {
  lab_instance_id: LabInstanceId;
  attempt_id: AttemptId;
  status: LabStatus;
  terminal_id: TerminalId | null;
  terminal_path: string;
  replaced_by_lab_instance_id: LabInstanceId | null;
}

export interface AttemptResult {
  outcome: "passed" | "failed";
  evaluation: StoredEvent<"evaluation.completed", EvaluationCompletedPayload>;
  evidence: StoredEvent<"evidence.created", EvidenceCreatedPayload>[];
  skill_updates: StoredEvent<"learner_skill.updated", LearnerSkillUpdatedPayload>[];
  completion: StoredEvent<"activity.completed", ActivityCompletedPayload>;
}

export interface AttemptState {
  attempt_id: AttemptId;
  session_id: SessionId;
  activity: ActivityView;
  status: "active" | "evaluating" | "completed";
  started_at: Timestamp;
  lab: LabState | null;
  last_submission_error: Problem | null;
  result: AttemptResult | null;
}

export type ClientEventRequest =
  | ClientEventBase<"content.highlighted", ContentHighlightedPayload>
  | ClientEventBase<"content.opened", ContentOpenedPayload>
  | ClientEventBase<"visualization.step_selected", VisualizationStepSelectedPayload>
  | ClientEventBase<"memo.edited", MemoEditedPayload>;

interface ClientEventBase<T extends string, P> {
  event_type: T;
  event_version: number;
  occurred_at: Timestamp;
  idempotency_key: string;
  attempt_id: AttemptId | null;
  payload: P;
}

export interface ChatMessageRequest {
  idempotency_key: string;
  occurred_at: Timestamp;
  thread_id: ThreadId | null;
  attempt_id: AttemptId | null;
  text: string;
  requested_mode?: Token | null;
  references: ChatReference[];
}

export interface ChatExchange {
  request: MessageRequestedEvent;
  reply: MessageGeneratedEvent;
  /** Memo written after this reply, for highlight threads only (or null). */
  memo?: MemoView | null;
}

export interface TimelinePage {
  events: StoredEvent[];
  last_position: number;
  has_more: boolean;
}

// ---------- pack documents served as ContentDocument.document ----------
// contracts/schemas/pack/visualization.json and reference.json. Read-only
// views; the UI tolerates missing optional fields.

export interface VisualizationObservation {
  argv: string[];
  purpose: string;
  look_for?: string;
}

export interface VisualizationReality {
  mechanism: string;
  observe: VisualizationObservation[];
  artifacts?: { kind: Token; locator: string; description: string }[];
}

export interface VisualizationStep {
  id: string;
  from: Token;
  to: Token;
  label: string;
  explanation?: string;
  reality?: VisualizationReality;
}

export interface VisualizationDocument {
  id: DefinitionId;
  version: VersionLabel;
  title: string;
  skills?: SkillId[];
  diagram: {
    type: "sequence";
    actors: { id: Token; label: string }[];
    steps: VisualizationStep[];
  };
}

export interface ReferenceDocument {
  id: DefinitionId;
  version: VersionLabel;
  title: string;
  summary?: string;
  aliases?: string[];
  skills?: SkillId[];
  sections?: { kind: string; body: string }[];
  media?: { uri: string; sha256: string; media_type: string; alt: string }[];
}
