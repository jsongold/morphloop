import type { Session, StoredEvent, Ws } from "../../types";

export const newestSessions = (sessions: Session[]): Session[] =>
  [...sessions].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));

export async function startSession(
  packId: string,
  topicId: string,
  post: <T>(path: string, body: unknown) => Promise<{ body: T }>,
): Promise<Session> {
  return (await post<Session>("/sessions", { pack_id: packId, topic_id: topicId })).body;
}

export async function sessionWs(
  sessionId: string,
  get: <T>(path: string, query?: Record<string, string>) => Promise<T>,
  post: <T>(path: string, body: unknown) => Promise<{ body: T }>,
): Promise<Ws> {
  const { items } = await get<{ items: Ws[] }>("/ws", { session_id: sessionId });
  if (items.length) return items.reduce((latest, item) => item.position > latest.position ? item : latest);
  const { body } = await post<StoredEvent>("/ws", { session_id: sessionId });
  return get<Ws>(`/ws/${body.ws_id}`);
}
