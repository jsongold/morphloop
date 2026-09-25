import assert from "node:assert/strict";
import test from "node:test";
import { attemptFor } from "./attempt.ts";

test("a failed chat send reuses its key only for the same thread and text", () => {
  let keys = 0;
  const next = () => String(++keys);
  const first = attemptFor(null, "/ws/one/threads/main/messages", "hello", next);
  assert.equal(attemptFor(first, first.path, "hello", next), first);
  assert.notEqual(attemptFor(first, first.path, "different", next).key, first.key);
  assert.notEqual(attemptFor(first, "/ws/two/threads/main/messages", "hello", next).key, first.key);
});
