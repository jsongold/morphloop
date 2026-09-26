import assert from "node:assert/strict";
import test from "node:test";
import { attemptFor, canAppend, canRetryLoad, clearIfUnchanged, mergeEntries } from "./attempt.ts";

test("a failed memo append reuses its key only for the same workspace and body", () => {
  let keys = 0;
  const next = () => String(++keys);
  const first = attemptFor(null, "ws_one", "note", next);
  assert.equal(attemptFor(first, "ws_one", "note", next), first);
  assert.notEqual(attemptFor(first, "ws_one", "new note", next).key, first.key);
  assert.notEqual(attemptFor(first, "ws_two", "note", next).key, first.key);
});

test("a stored entry remains visible in append order when refresh fails", () => {
  const older = { entry_id: "old", actor: "learner", body: "old", position: 1 };
  const newer = { entry_id: "new", actor: "learner", body: "new", position: 2 };
  assert.deepEqual(mergeEntries([newer], older), [older, newer]);
  assert.deepEqual(mergeEntries([older, newer], newer), [older, newer]);
});

test("a newer memo draft survives completion of the prior append", () => {
  assert.equal(clearIfUnchanged("next note", "first note"), "next note");
  assert.equal(clearIfUnchanged("first note", "first note"), "");
});

test("adding a note waits for the active workspace's entries to settle", () => {
  assert.equal(canAppend("ws_one", "ws_one", "note", false), true);
  assert.equal(canAppend(null, "ws_one", "note", false), false);
  assert.equal(canAppend("ws_two", "ws_one", "note", false), false);
  assert.equal(canAppend("ws_one", "ws_one", "  ", false), false);
  assert.equal(canAppend("ws_one", "ws_one", "note", true), false);
});

test("a failed initial entries load offers a retry, a failed append does not", () => {
  assert.equal(canRetryLoad(null, "ws_one", "boom"), true);
  assert.equal(canRetryLoad("ws_two", "ws_one", "boom"), true);
  assert.equal(canRetryLoad("ws_one", "ws_one", "boom"), false);
  assert.equal(canRetryLoad(null, null, "boom"), false);
  assert.equal(canRetryLoad(null, "ws_one", null), false);
});
