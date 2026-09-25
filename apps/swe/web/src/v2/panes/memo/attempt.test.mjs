import assert from "node:assert/strict";
import test from "node:test";
import { attemptFor, clearIfUnchanged, mergeEntries } from "./attempt.ts";

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
