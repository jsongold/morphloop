import assert from "node:assert/strict";
import test from "node:test";
import {
  attemptFor,
  canRetryLoad,
  canSend,
  clearIfUnchanged,
  isHintThread,
  keyForWorkspace,
  mergeMessages,
} from "./attempt.ts";

test("a failed chat send reuses its key only for the same thread and text", () => {
  let keys = 0;
  const next = () => String(++keys);
  const first = attemptFor(null, "/ws/one/threads/main/messages", "hello", next);
  assert.equal(attemptFor(first, first.path, "hello", next), first);
  assert.notEqual(attemptFor(first, first.path, "different", next).key, first.key);
  assert.notEqual(attemptFor(first, "/ws/two/threads/main/messages", "hello", next).key, first.key);
});

test("main-thread creation keeps one key across effect replays and retries", () => {
  const keys = new Map();
  let calls = 0;
  const next = () => String(++calls);
  assert.equal(keyForWorkspace(keys, "ws_one", next), keyForWorkspace(keys, "ws_one", next));
  assert.equal(calls, 1);
  assert.notEqual(keyForWorkspace(keys, "ws_two", next), keys.get("ws_one"));
});

test("a successful exchange remains visible and does not duplicate messages", () => {
  const sent = { message_id: "sent", role: "learner", text: "Hi", created_at: "" };
  const reply = { message_id: "reply", role: "assistant", text: "Hello", created_at: "" };
  assert.deepEqual(mergeMessages([sent], [sent, reply]), [sent, reply]);
});

test("a newer chat draft survives completion of the prior send", () => {
  assert.equal(clearIfUnchanged("next question", "first question"), "next question");
  assert.equal(clearIfUnchanged("first question", "first question"), "");
});

test("sending waits for the active thread's history to settle", () => {
  const path = "/ws/one/threads/main/messages";
  assert.equal(canSend(path, path, "hi", false), true);
  assert.equal(canSend(null, path, "hi", false), false);
  assert.equal(canSend("/ws/one/threads/other/messages", path, "hi", false), false);
  assert.equal(canSend(path, path, "  ", false), false);
  assert.equal(canSend(path, path, "hi", true), false);
});

test("a failed initial history load offers a retry, a failed send does not", () => {
  const path = "/ws/one/threads/main/messages";
  assert.equal(canRetryLoad(null, path, "boom"), true);
  assert.equal(canRetryLoad("/ws/one/threads/other/messages", path, "boom"), true);
  assert.equal(canRetryLoad(path, path, "boom"), false);
  assert.equal(canRetryLoad(null, null, "boom"), false);
  assert.equal(canRetryLoad(null, path, null), false);
});

test("hint mode is read from the active thread's own labels, not the main thread's", () => {
  const labels = new Map([
    ["main_1", undefined],
    ["hint_1", ["mode:hint"]],
  ]);
  assert.equal(isHintThread(labels, "hint_1"), true);
  assert.equal(isHintThread(labels, "main_1"), false);
  assert.equal(isHintThread(labels, "unseen_thread"), false);
  assert.equal(isHintThread(labels, null), false);
});
