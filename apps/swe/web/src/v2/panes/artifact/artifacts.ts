// Artifact resource calls for the artifact pane (#115). Kept out of the
// components so the request shapes are unit-testable with the `get`/`post`
// from src/v2/api injected (same pattern as panes/session/flow.ts).
//
// The learner-safe artifact spec (GET /artifact-specs/{spec_id}) is the only
// per-type view: a diagram's `spec` is the diagram to draw; a lab's `spec` is
// `{}` (fixture, params and check ids stay hidden). Check ids are therefore not
// enumerable from the client: the learner runs one by naming it.

export type Artifact = {
  artifact_id: string;
  type: string;
  spec_id: string;
  ws_id: string;
  status: "running" | "stopped";
  lab: { lab_instance_id: string } | null;
};

export type ArtifactSummary = {
  artifact_id: string;
  type: string;
  spec_id: string;
  status: "running" | "stopped";
};

export type ArtifactSpec = {
  id: string;
  type: string;
  labels: string[];
  spec: Record<string, unknown>;
};

export type CheckResult = {
  artifact_id: string;
  check_id: string;
  passed: boolean;
  observed: Record<string, unknown>;
};

export type CheckBody = { check_id: string; params: Record<string, unknown> };

type Get = <T>(path: string, query?: Record<string, string>) => Promise<T>;
type Post = <T>(path: string, body?: unknown) => Promise<{ body: T }>;

const id = (value: string): string => encodeURIComponent(value);

export function artifactSpec(get: Get, specId: string): Promise<ArtifactSpec> {
  return get<ArtifactSpec>(`/artifact-specs/${id(specId)}`);
}

/** The newest running artifact for this ws (optionally one spec), or null.
 *
 * The list is creation-ordered oldest first, so the last running item is the
 * one to reuse instead of starting a new lab. */
export async function openArtifact(
  get: Get,
  wsId: string,
  specId?: string,
): Promise<Artifact | null> {
  const query: Record<string, string> = specId === undefined ? {} : { spec_id: specId };
  const { items } = await get<{ items: ArtifactSummary[] }>(
    `/ws/${id(wsId)}/artifacts`,
    query,
  );
  const running = items.filter((item) => item.status === "running");
  const latest = running[running.length - 1];
  if (!latest) return null;
  return get<Artifact>(`/ws/${id(wsId)}/artifacts/${id(latest.artifact_id)}`);
}

export async function startLab(post: Post, wsId: string, specId: string): Promise<Artifact> {
  return (await post<Artifact>(`/ws/${id(wsId)}/artifacts`, { spec_id: specId })).body;
}

export async function labAction(
  post: Post,
  wsId: string,
  artifactId: string,
  action: "reset" | "stop",
): Promise<Artifact> {
  return (
    await post<Artifact>(`/ws/${id(wsId)}/artifacts/${id(artifactId)}/${action}`)
  ).body;
}

export async function runCheck(
  post: Post,
  wsId: string,
  artifactId: string,
  body: CheckBody,
): Promise<CheckResult> {
  return (
    await post<CheckResult>(`/ws/${id(wsId)}/artifacts/${id(artifactId)}/check`, body)
  ).body;
}

export function parseParams(text: string): { params?: Record<string, unknown>; error?: string } {
  const trimmed = text.trim();
  if (!trimmed) return { params: {} };
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch (cause) {
    return { error: cause instanceof Error ? cause.message : String(cause) };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { error: "Params must be a JSON object." };
  }
  return { params: value as Record<string, unknown> };
}

/** The check request a learner's typed check id and params text produce. */
export function checkRequest(
  checkId: string,
  paramsText: string,
): { body?: CheckBody; error?: string } {
  const trimmed = checkId.trim();
  if (!trimmed) return { error: "Enter a check id." };
  const parsed = parseParams(paramsText);
  if (parsed.error) return { error: parsed.error };
  return { body: { check_id: trimmed, params: parsed.params ?? {} } };
}
