import assert from "node:assert/strict";
import test from "node:test";
import { tocSections, topicIds } from "./toc.ts";

const doc = (id) => ({ id, title: id, labels: [] });
const idsOf = (sections) => sections.flatMap((s) => s.docs.map((d) => d.id));

test("a parent topic with 3 child topics yields their docs in depth-first order", () => {
  const tree = {
    id: "net.dns.resolution",
    title: "DNS name resolution",
    topics: [
      { id: "lookup-path", title: "How an application looks up a name", docs: ["doc.lookup"] },
      { id: "resolver", title: "Stub resolver", docs: ["doc.resolver.a", "doc.resolver.b"] },
      { id: "answers", title: "Reading a DNS answer", docs: ["doc.answers"] },
    ],
  };
  const docs = new Map([
    ["lookup-path", [doc("doc.lookup")]],
    ["resolver", [doc("doc.resolver.a"), doc("doc.resolver.b")]],
    ["answers", [doc("doc.answers")]],
  ]);
  const sections = tocSections(tree, docs);
  assert.deepEqual(topicIds(tree), ["net.dns.resolution", "lookup-path", "resolver", "answers"]);
  assert.deepEqual(idsOf(sections), ["doc.lookup", "doc.resolver.a", "doc.resolver.b", "doc.answers"]);
  assert.deepEqual(sections.map((s) => [s.title, s.depth]), [
    ["How an application looks up a name", 1],
    ["Stub resolver", 1],
    ["Reading a DNS answer", 1],
  ]);
  assert.equal(sections.some((s) => s.id === "net.dns.resolution"), false);
});

test("a topic's own docs come before its subtopics, which stay depth-first", () => {
  const tree = {
    id: "root",
    title: "Root",
    docs: ["r0"],
    topics: [
      { id: "a", title: "A", docs: ["a0"], topics: [{ id: "a.x", title: "A.X", docs: ["x0"] }] },
      { id: "b", title: "B", docs: ["b0"] },
    ],
  };
  const docs = new Map([
    ["root", [doc("r0")]],
    ["a", [doc("a0")]],
    ["a.x", [doc("x0")]],
    ["b", [doc("b0")]],
  ]);
  const sections = tocSections(tree, docs);
  assert.deepEqual(idsOf(sections), ["r0", "a0", "x0", "b0"]);
  assert.deepEqual(sections.map((s) => s.depth), [0, 1, 2, 1]);
});

test("topics without docs contribute no section", () => {
  const tree = { id: "root", title: "Root", topics: [{ id: "empty", title: "Empty" }] };
  assert.deepEqual(tocSections(tree, new Map()), []);
});
