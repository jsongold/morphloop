import assert from "node:assert/strict";
import test from "node:test";
import { answerRequest, drillPath } from "./request.ts";

test("drill labels are repeated query parameters", () => {
  assert.equal(drillPath([]), "/drills");
  assert.equal(drillPath(["topic:network.dns", "origin:pack"]), "/drills?labels=topic%3Anetwork.dns&labels=origin%3Apack");
});

test("text, choice, and artifact answers keep the retry key only for the same action", () => {
  let next = 0;
  const key = () => `key-${++next}`;
  const text = { id: "one", answer_mode: "text" };
  const choice = { id: "two", answer_mode: "choice", choices: ["yes", "no"] };
  const first = answerRequest("ws_a", text, "  hello  ", null, key);
  assert.deepEqual(first.body, { actual: "hello" });
  assert.equal(answerRequest("ws_a", text, "hello", first, key).key, first.key);
  assert.notEqual(answerRequest("ws_b", text, "hello", first, key).key, first.key);
  assert.equal(answerRequest("ws_a", text, "   ", null, key), null);
  assert.equal(answerRequest("ws_a", choice, "maybe", null, key), null);
  assert.deepEqual(answerRequest("ws_a", choice, "yes", null, key).body, { actual: "yes" });
  const artifact = { id: "three", answer_mode: "artifact" };
  const lab = answerRequest("ws_a", artifact, "art_1", null, key);
  assert.deepEqual(lab.body, { artifact_id: "art_1" });
  assert.equal(answerRequest("ws_a", artifact, "art_1", lab, key).key, lab.key);
  assert.notEqual(answerRequest("ws_a", artifact, "art_2", lab, key).key, lab.key);
  assert.equal(answerRequest("ws_a", artifact, "invalid", null, key), null);
});

test("CommonMark renders formatting without enabling raw HTML", async (t) => {
  let renderMarkdown;
  try {
    ({ renderMarkdown } = await import("./markdown.ts"));
  } catch (error) {
    if (error.code === "ERR_MODULE_NOT_FOUND" && error.message.includes("markdown-it")) return t.skip("markdown-it dependency PR has not landed");
    throw error;
  }
  const html = renderMarkdown("**bold** <script>alert(1)</script>");
  assert.match(html, /<strong>bold<\/strong>/);
  assert.doesNotMatch(html, /<script>/);
});
