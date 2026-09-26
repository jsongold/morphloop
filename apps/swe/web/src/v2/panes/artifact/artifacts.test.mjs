import assert from "node:assert/strict";
import test from "node:test";
import {
  artifactSpec,
  checkRequest,
  labAction,
  openArtifact,
  parseParams,
  runCheck,
  startLab,
} from "./artifacts.ts";

const artifact = { artifact_id: "art_2", type: "lab", spec_id: "lab_spec", ws_id: "ws_1", status: "running", lab: { lab_instance_id: "lab_2" } };

test("openArtifact reuses the newest running lab, filtered by spec", async () => {
  const calls = [];
  const items = [
    { artifact_id: "art_1", status: "stopped" },
    { artifact_id: "art_2", status: "running" },
    { artifact_id: "art_3", status: "running" },
    { artifact_id: "art_4", status: "stopped" },
  ];
  const result = await openArtifact(async (path, query) => {
    calls.push(["GET", path, query]);
    return path === "/ws/ws_1/artifacts" ? { items } : artifact;
  }, "ws_1", "lab_spec");
  assert.equal(result, artifact);
  assert.deepEqual(calls, [
    ["GET", "/ws/ws_1/artifacts", { spec_id: "lab_spec" }],
    ["GET", "/ws/ws_1/artifacts/art_3", undefined],
  ]);
});

test("openArtifact returns null when no lab is running and never GETs one", async () => {
  const paths = [];
  const result = await openArtifact(async (path, query) => {
    paths.push([path, query]);
    return { items: [{ artifact_id: "art_1", status: "stopped" }] };
  }, "ws_1");
  assert.equal(result, null);
  assert.deepEqual(paths, [["/ws/ws_1/artifacts", {}]]);
});

test("startLab posts the spec id and returns the started artifact", async () => {
  const calls = [];
  const result = await startLab(async (path, body) => {
    calls.push([path, body]);
    return { body: artifact };
  }, "ws_1", "lab_spec");
  assert.equal(result, artifact);
  assert.deepEqual(calls, [["/ws/ws_1/artifacts", { spec_id: "lab_spec" }]]);
});

test("reset and stop post the artifact action path", async () => {
  const calls = [];
  const post = async (path) => {
    calls.push(path);
    return { body: artifact };
  };
  assert.equal(await labAction(post, "ws_1", "art_2", "reset"), artifact);
  assert.equal(await labAction(post, "ws_1", "art_2", "stop"), artifact);
  assert.deepEqual(calls, ["/ws/ws_1/artifacts/art_2/reset", "/ws/ws_1/artifacts/art_2/stop"]);
});

test("runCheck posts the check body and returns the observation", async () => {
  const calls = [];
  const checked = { artifact_id: "art_2", check_id: "dns.http_status", passed: true, observed: { status: 200 } };
  const result = await runCheck(async (path, body) => {
    calls.push([path, body]);
    return { body: checked };
  }, "ws_1", "art_2", { check_id: "dns.http_status", params: { url: "http://ledger/readyz" } });
  assert.equal(result, checked);
  assert.deepEqual(calls, [["/ws/ws_1/artifacts/art_2/check", { check_id: "dns.http_status", params: { url: "http://ledger/readyz" } }]]);
});

test("the artifact spec is read by id", async () => {
  const calls = [];
  const spec = { id: "dns-resolution-flow", type: "diagram", labels: [], spec: {} };
  assert.equal(await artifactSpec(async (path) => { calls.push(path); return spec; }, "dns-resolution-flow"), spec);
  assert.deepEqual(calls, ["/artifact-specs/dns-resolution-flow"]);
});

test("params parse as a JSON object; blank text has none; bad input is a message, not a throw", () => {
  assert.deepEqual(parseParams("  "), {});
  assert.deepEqual(parseParams("{}"), { params: {} });
  assert.deepEqual(parseParams('{"name":"ledger"}'), { params: { name: "ledger" } });
  assert.equal(typeof parseParams("{").error, "string");
  assert.equal(typeof parseParams("[1,2]").error, "string");
  assert.equal(typeof parseParams("null").error, "string");
});

test("a check needs a non-empty id; blank params are omitted so the check defaults to the spec's target (#149)", () => {
  assert.deepEqual(checkRequest("dns.name_resolves", '{"name":"ledger"}'), {
    body: { check_id: "dns.name_resolves", params: { name: "ledger" } },
  });
  assert.deepEqual(checkRequest("dns.name_resolves", "  "), {
    body: { check_id: "dns.name_resolves" },
  });
  assert.deepEqual(checkRequest("dns.name_resolves", "{}"), {
    body: { check_id: "dns.name_resolves", params: {} },
  });
  assert.equal(checkRequest("  ", "").error, "Enter a check id.");
  assert.equal(typeof checkRequest("dns.name_resolves", "oops").error, "string");
});
