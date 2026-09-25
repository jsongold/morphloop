import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
const exports = {};
const fakeRequire = (name) => name === "@/v2/api" || name === "@/v2/state" || name === "../artifact" ? {} : require(name);
vm.runInNewContext(code, { exports, require: fakeRequire });

function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(text).join("");
  if (typeof node !== "object") return String(node);
  return text(node.props.children);
}

for (const file of readdirSync("../../../contracts/fixtures/plaintext")) {
  const fixture = JSON.parse(readFileSync(`../../../contracts/fixtures/plaintext/${file}`, "utf8"));
  const result = exports.renderBlock(fixture.markdown);
  assert.equal(text(result.content), fixture.plaintext, file);
  assert.equal(result.artifacts.length, (file.startsWith("artifact-") && !file.includes("lookalike") && !file.includes("multiline") ? 1 : 0), file);
}
