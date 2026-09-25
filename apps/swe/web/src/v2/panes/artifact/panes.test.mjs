import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);

function loadPane() {
  const ts = require("typescript");
  const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const mod = { exports: {} };
  const imports = (id) => {
    if (id === "../../state") return { useWorkspace: () => ({ ws: { ws_id: "ws_test" } }) };
    if (id === "../../api") return { get: async () => ({}), post: async () => ({ body: {} }) };
    if (id === "@/components/TerminalPane") return { default: () => null };
    if (id === "../types") return {};
    if (id === "./artifacts") return {
      artifactSpec: async () => ({ type: "diagram", spec: {} }),
      checkRequest: () => ({}), labAction: async () => ({}), openArtifact: async () => null,
      runCheck: async () => ({}), startLab: async () => ({}),
    };
    return require(id);
  };
  runInNewContext(`(function(require, module, exports) { ${js} })`)(imports, mod, mod.exports);
  return mod.exports;
}

const pane = loadPane();
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const slot = (type, ref) => renderToStaticMarkup(React.createElement(pane.ArtifactSlot, { directive: { type, ref } }));

test("a directive's type selects its renderer", () => {
  assert.match(slot("lab", "diagnose-dns-resolver-misconfiguration-lab"), /Start lab/);
  assert.match(slot("diagram", "dns-resolution-flow"), /Loading diagram/);
});

test("an unregistered type falls back instead of throwing", () => {
  assert.match(slot("mystery", "x"), /Unsupported artifact: mystery/);
});

test("registerArtifactRenderer registers a type's renderer", () => {
  pane.registerArtifactRenderer("probe", () => React.createElement("b", null, "probed"));
  assert.match(slot("probe", "x"), /probed/);
});

test("the diagram carries its stroke styles into the shadow tree", () => {
  const spec = {
    title: "DNS resolution",
    diagram: {
      actors: [{ id: "client", label: "Client" }, { id: "resolver", label: "Resolver" }],
      steps: [{ id: "s1", from: "client", to: "resolver", label: "query", explanation: "asks" }],
    },
  };
  const html = renderToStaticMarkup(React.createElement(pane.DiagramContent, { spec }));
  assert.match(html, /\.viz-lifeline\s*\{[^}]*stroke:\s*var\(--border\)/);
  assert.match(html, /\.viz-step line,\s*\.viz-step path\s*\{[^}]*stroke:\s*currentColor/);
  assert.match(html, /class="viz-lifeline"/);
});
