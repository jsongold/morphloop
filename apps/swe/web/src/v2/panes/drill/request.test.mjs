import assert from "node:assert/strict";
import test from "node:test";
import { answeredItems, answerRequest, artifactOptions, drillArtifactRefs, drillPath } from "./request.ts";

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

test("answers loaded from the API mark their items answered", () => {
  assert.deepEqual(
    answeredItems([
      { item_id: "one", answer_event_id: "ev_1", judgment_status: "unjudged", gap: null },
      { item_id: "two", answer_event_id: "ev_2", judgment_status: "judged", gap: { missing: "dns" } },
    ]),
    { one: "ev_1", two: "ev_2" },
  );
});

test("artifact items ask only for the specs they reference and offer matching artifacts", () => {
  const items = [
    { id: "a", answer_mode: "artifact", artifact_ref: "spec_one" },
    { id: "b", answer_mode: "artifact", artifact_ref: "spec_one" },
    { id: "c", answer_mode: "text" },
    { id: "d", answer_mode: "artifact", artifact_ref: "spec_two" },
  ];
  assert.deepEqual(drillArtifactRefs(items), ["spec_one", "spec_two"]);
  const artifacts = [
    { artifact_id: "art_1", type: "lab", spec_id: "spec_one", status: "running" },
    { artifact_id: "art_2", type: "lab", spec_id: "spec_two", status: "stopped" },
  ];
  assert.deepEqual(artifactOptions(artifacts, "spec_one").map((a) => a.artifact_id), ["art_1"]);
  assert.deepEqual(artifactOptions(artifacts, "spec_missing"), []);
});

test("CommonMark renders formatting without enabling raw HTML", async () => {
  const { renderMarkdown } = await import("./markdown.ts");
  const html = renderMarkdown("**bold** <script>alert(1)</script>");
  assert.match(html, /<strong>bold<\/strong>/);
  assert.doesNotMatch(html, /<script>/);
});
