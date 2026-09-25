import assert from "node:assert/strict";
import test from "node:test";
import { attemptFor } from "./attempt.ts";

test("a failed memo append reuses its key only for the same workspace and body", () => {
  let keys = 0;
  const next = () => String(++keys);
  const first = attemptFor(null, "ws_one", "note", next);
  assert.equal(attemptFor(first, "ws_one", "note", next), first);
  assert.notEqual(attemptFor(first, "ws_one", "new note", next).key, first.key);
  assert.notEqual(attemptFor(first, "ws_two", "note", next).key, first.key);
});
