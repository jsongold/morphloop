import assert from "node:assert/strict";
import { test } from "node:test";
import { highlightKey, keepsDraft, nearBottom, popupTop, threadTarget } from "./behavior.ts";

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
