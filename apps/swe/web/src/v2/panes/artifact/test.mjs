import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
const exports = {};
const fakeRequire = (name) => name === "@/v2/api" || name === "@/v2/state" ? {} : require(name);
vm.runInNewContext(code, { exports, require: fakeRequire });

function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node !== "object") return String(node);
  return text(node.props.children);
}

// A "lab" directive (apps/swe/pack/textbooks/network.dns.resolver.json) must resolve to its
// registered renderer, not ArtifactSlot's unregistered-type placeholder (#141 P1 finding).
const labSlot = exports.ArtifactSlot({ directive: { type: "lab", ref: "diagnose-dns-resolver-misconfiguration-lab" } });
assert.equal(typeof labSlot.type, "function", 'no renderer registered for "lab"; ArtifactSlot fell back to its placeholder');

// An unregistered type still falls back (regression guard on the placeholder itself).
const unknownSlot = exports.ArtifactSlot({ directive: { type: "made-up-type", ref: "x" } });
assert.equal(unknownSlot.type, "div");
assert.match(text(unknownSlot), /no renderer registered/);
