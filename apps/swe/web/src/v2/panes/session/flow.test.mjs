import assert from "node:assert/strict";
import test from "node:test";
import { newestSessions, sessionWs, startSession } from "./flow.ts";

test("sessions show newest first", () => {
  const older = { id: "older", created_at: "2026-01-01T00:00:00Z" };
  const newer = { id: "newer", created_at: "2026-01-02T00:00:00Z" };
  assert.deepEqual(newestSessions([older, newer]).map((s) => s.id), ["newer", "older"]);
});

test("start sends the chosen pack and nested topic", async () => {
  const calls = [];
  const session = { id: "ses_1" };
  const result = await startSession("pack", "root.child", async (path, body) => {
    calls.push([path, body]);
    return { body: session };
  });
  assert.equal(result, session);
  assert.deepEqual(calls, [["/sessions", { pack_id: "pack", topic_id: "root.child" }]]);
});

test("reopen selects the latest workspace", async () => {
  const calls = [];
  const latest = { ws_id: "latest", position: 3 };
  const result = await sessionWs("ses_1", async (path, query) => {
    calls.push([path, query]);
    return { items: [latest, { ws_id: "old", position: 1 }] };
  }, async () => { throw Error("must not create a workspace"); });
  assert.equal(result, latest);
  assert.deepEqual(calls, [["/ws", { session_id: "ses_1" }]]);
});

test("opening a session without a workspace creates and reads one", async () => {
  const calls = [];
  const result = await sessionWs("ses_1", async (path, query) => {
    calls.push(["GET", path, query]);
    return path === "/ws" ? { items: [] } : { ws_id: "ws_1" };
  }, async (path, body) => {
    calls.push(["POST", path, body]);
    return { body: { ws_id: "ws_1" } };
  });
  assert.equal(result.ws_id, "ws_1");
  assert.deepEqual(calls, [
    ["GET", "/ws", { session_id: "ses_1" }],
    ["POST", "/ws", { session_id: "ses_1" }],
    ["GET", "/ws/ws_1", undefined],
  ]);
});
