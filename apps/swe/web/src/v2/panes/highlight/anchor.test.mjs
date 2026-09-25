import assert from "node:assert/strict";
import { test } from "node:test";
import { anchorFor } from "./anchor.ts";

test("a block selection saves quote and Unicode character offsets", () => {
  const anchor = anchorFor("doc", "block", "A😀BC", 1, 3);
  assert.deepEqual(anchor?.selector, [
    { type: "TextQuoteSelector", exact: "😀B", prefix: "A", suffix: "C" },
    { type: "TextPositionSelector", start: 1, end: 3 },
  ]);
  assert.equal(anchorFor("doc", "block", "ABC", 1, 1), null);
  assert.equal(anchorFor("doc", "block", "ABC", 1, 5), null);
});
