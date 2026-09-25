import assert from "node:assert/strict";
import test from "node:test";
import { answerRequest, drillPath } from "./request.ts";

test("drill labels are repeated query parameters", () => {
  assert.equal(drillPath([]), "/drills");
  assert.equal(drillPath(["topic:network.dns", "origin:pack"]), "/drills?labels=topic%3Anetwork.dns&labels=origin%3Apack");
});

test("text and choice answers keep the retry key only for the same action", () => {
  let next = 0;
  const key = () => `key-${++next}`;
  const text = { id: "one", answer_mode: "text" };
  const choice = { id: "two", answer_mode: "choice", choices: ["yes", "no"] };
  const first = answerRequest("ws_a", text, "  hello  ", null, key);
  assert.equal(first.actual, "hello");
  assert.equal(answerRequest("ws_a", text, "hello", first, key).key, first.key);
  assert.notEqual(answerRequest("ws_b", text, "hello", first, key).key, first.key);
  assert.equal(answerRequest("ws_a", text, "   ", null, key), null);
  assert.equal(answerRequest("ws_a", choice, "maybe", null, key), null);
  assert.equal(answerRequest("ws_a", choice, "yes", null, key).actual, "yes");
  assert.equal(answerRequest("ws_a", { id: "three", answer_mode: "artifact" }, "art_1", null, key), null);
});
