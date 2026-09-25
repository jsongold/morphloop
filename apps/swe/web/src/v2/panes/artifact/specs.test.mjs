import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url)));

test("bundled diagram and lab checks match the SWE pack", () => {
  const bundled = read("./specs.json");
  const diagram = read("../../../../../pack/artifacts/dns-resolution-flow.json");
  const lab = read("../../../../../pack/artifacts/diagnose-dns-resolver-misconfiguration-lab.json");
  const params = lab.spec.environment.params;
  assert.deepEqual(bundled.diagram, diagram);
  assert.equal(bundled.lab.id, lab.id);
  assert.deepEqual(bundled.lab.checks, [
    { id: "dns.resolver_answers", params: { name: params.service_name, record_type: "A", expected_value: params.service_address } },
    { id: "dns.name_resolves", params: { name: params.service_name, expected_address: params.service_address } },
    { id: "dns.http_status", params: { url: `http://${params.service_name}:${params.service_port}${params.health_path}`, expected_status: 200 } },
  ]);
  assert.deepEqual(bundled.lab.checks.map(({ id }) => id), lab.spec.allowed_checks);
});

test("artifact slots render the registered lab and diagram", () => {
  const ts = require("typescript");
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const mod = { exports: {} };
  const imports = (id) => {
    if (id === "../../state") return { useWorkspace: () => ({ ws: { ws_id: "ws_test" } }) };
    if (id === "../../api") return { get() {}, post() {} };
    if (id === "@/components/TerminalPane") return { default() {} };
    if (id === "./specs.json") return read("./specs.json");
    return require(id);
  };
  runInNewContext(`(function(require, module, exports) { ${js} })`)(imports, mod, mod.exports);
  const slot = (type, ref) => renderToStaticMarkup(React.createElement(mod.exports.ArtifactSlot, { directive: { type, ref } }));
  assert.match(slot("lab", read("./specs.json").lab.id), /Start lab/);
  assert.match(slot("diagram", read("./specs.json").diagram.id), /Sequence diagram: How ledger/);
});
