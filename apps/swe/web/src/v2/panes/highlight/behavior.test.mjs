import assert from "node:assert/strict";
import { test } from "node:test";
import { highlightKey, keepsDraft, mergeById, nearBottom, popupTop, reuseKey, shouldClearDraft, threadTarget } from "./behavior.ts";

const anchor = {
  doc_id: "doc",
  block_id: "block",
  selector: [
    { type: "TextQuoteSelector", exact: "quoted text", prefix: "", suffix: "" },
    { type: "TextPositionSelector", start: 0, end: 11 },
  ],
};

test("the thread target contains the quote that the assistant reads", () => {
  assert.deepEqual(threadTarget("hl_1", anchor), {
    kind: "textbook_block", doc_id: "doc", block_id: "block", highlight_id: "hl_1", selector: anchor.selector,
  });
});

test("an uncertain highlight creation reuses its key only for the same selection", () => {
  let next = 0;
  const newKey = () => String(++next);
  const first = highlightKey(null, "ws_1", anchor, newKey);
  assert.equal(highlightKey(first, "ws_1", anchor, newKey).key, first.key);
  assert.notEqual(highlightKey(first, "ws_2", anchor, newKey).key, first.key);
  assert.notEqual(highlightKey(first, "ws_1", { ...anchor, block_id: "other" }, newKey).key, first.key);
});

test("popup placement stays inside the viewport", () => {
  assert.equal(popupTop(790, 800, 300), 492);
  assert.equal(popupTop(2, 800, 300), 8);
});

test("chat only follows new messages when already near the bottom", () => {
  assert.equal(nearBottom(670, 300, 1000), true);
  assert.equal(nearBottom(200, 300, 1000), false);
});

test("an unsent draft does not follow the popup to a different thread", () => {
  assert.equal(keepsDraft(null, "t1"), false);
  assert.equal(keepsDraft("t1", "t1"), true);
  assert.equal(keepsDraft("t1", "t2"), false);
});

test("a response clears the composer only if the submitted text is still there", () => {
  assert.equal(shouldClearDraft("question", "question"), true);
  assert.equal(shouldClearDraft("  question  ", "question"), true);
  assert.equal(shouldClearDraft("follow-up", "question"), false);
  assert.equal(shouldClearDraft("", "question"), false);
});

test("an uncertain delete reuses its key until the action succeeds", () => {
  let next = 0;
  const newKey = () => String(++next);
  const keys = new Map();
  const first = reuseKey(keys, "hl_1", newKey);
  assert.equal(reuseKey(keys, "hl_1", newKey), first);
  assert.notEqual(reuseKey(keys, "hl_2", newKey), first);
  keys.delete("hl_1");
  assert.notEqual(reuseKey(keys, "hl_1", newKey), first);
});

test("messages loaded while a send is pending are merged, not duplicated", () => {
  const messages = [
    { message_id: "m1", role: "learner", text: "q" },
    { message_id: "m2", role: "assistant", text: "a" },
  ];
  const sent = { message_id: "m2", role: "learner", text: "q" };
  const reply = { message_id: "m3", role: "assistant", text: "a2" };
  assert.deepEqual(mergeById(messages, [sent, reply], (m) => m.message_id), [...messages, reply]);
});

test("a highlight saved during a slow load survives the merge", () => {
  const loaded = [{ highlight_id: "hl_1" }, { highlight_id: "hl_2" }];
  const saved = [{ highlight_id: "hl_3", anchor: "new" }];
  assert.deepEqual(mergeById(loaded, saved, (h) => h.highlight_id), [...loaded, ...saved]);
  const localWithDuplicate = [{ highlight_id: "hl_1", anchor: "local" }, ...saved];
  assert.deepEqual(mergeById(loaded, localWithDuplicate, (h) => h.highlight_id), [...loaded, ...saved]);
});
