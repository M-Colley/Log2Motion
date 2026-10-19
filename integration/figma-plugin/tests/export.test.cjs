const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const { transformSync } = require("esbuild");

function plugin(nodes = new Map()) {
  const events = {};
  const context = vm.createContext({
    __html__: "", console, setTimeout,
    figma: {
      showUI() {}, ui: { postMessage() {} },
      root: { name: "Fixture" }, currentPage: { name: "Page", selection: [], findAll() { return []; } },
      loadAllPagesAsync: async () => {},
      on: (type, listener) => { events[type] = listener; },
      getNodeByIdAsync: async (id) => nodes.get(id),
      base64Encode: () => "fixture",
    },
  });
  vm.runInContext(transformSync(fs.readFileSync("code.ts", "utf8"), { loader: "ts", target: "es2020" }).code, context);
  return { context, events };
}

function fixtures() {
  const screen = { id: "screen", type: "FRAME", width: 100, height: 200,
    absoluteTransform: [[1, 0, 500], [0, 1, 300]], parent: { type: "PAGE" } };
  const node = { id: "button", name: "Button", type: "RECTANGLE", x: 10, y: 20, width: 20, height: 40,
    absoluteTransform: [[1, 0, 510], [0, 1, 320]], parent: screen,
    exportAsync: async () => new Uint8Array(), getPluginData: () => "" };
  return { screen, node };
}

test("exports the selected center relative to the screen frame", () => {
  const { context } = plugin();
  context.node = fixtures().node;
  assert.deepEqual(Array.from(vm.runInContext("normalizedCenter(node)", context)), [0.2, 0.2]);
});

test("handles rotated screen and nested node transforms", () => {
  const { context } = plugin();
  const { screen, node } = fixtures();
  screen.absoluteTransform = [[0, -1, 500], [1, 0, 300]];
  node.absoluteTransform = [[0, -1, 480], [1, 0, 310]];
  node.parent = { type: "GROUP", parent: screen };
  context.node = node;
  assert.deepEqual(Array.from(vm.runInContext("normalizedCenter(node)", context)), [0.2, 0.2]);
});

test("retains repeated selections in the export", async () => {
  const { node } = fixtures();
  const { context } = plugin(new Map([[node.id, node]]));
  const result = await vm.runInContext('clickPath.push({id:"button",name:"Button"}, {id:"button",name:"Button"}); collectSelectedNodes()', context);
  assert.equal(result.length, 2);
  assert.deepEqual(Array.from(result[0].normalizedCenter), [0.2, 0.2]);
});

test("cleanup preserves selection changes during a pending node lookup", async () => {
  const { context } = plugin();
  let finishLookup;
  context.figma.getNodeByIdAsync = (id) => {
    assert.equal(id, "A");
    return new Promise((resolve) => { finishLookup = resolve; });
  };
  const cleanup = vm.runInContext('clickPath.push({id:"A",name:"A"}); cleanupClickPath()', context);
  assert.equal(typeof finishLookup, "function");
  vm.runInContext('clickPath.length = 0; clickPath.push({id:"B",name:"B"})', context);
  finishLookup({ id: "A" });
  await cleanup;
  assert.deepEqual(Array.from(vm.runInContext("clickPath.map((entry) => entry.id)", context)), ["B"]);
});

test("rejects missing screen geometry instead of simulating a center tap", () => {
  const { context } = plugin();
  context.node = { ...fixtures().node, parent: { type: "PAGE" } };
  assert.throws(() => vm.runInContext("normalizedCenter(node)", context), /screen frame/);
});

test("allows ordinary user vectors into the click path", () => {
  const { node } = fixtures();
  node.name = "Vector → next";
  const { context, events } = plugin(new Map([[node.id, node]]));
  context.figma.currentPage.selection = [node];
  vm.runInContext("selectionMode = true; showArrowsGlobally = false", context);
  events.selectionchange();
  assert.equal(vm.runInContext("clickPath.length", context), 1);
});

test("finish-selection cleanup preserves ordinary design vectors", async () => {
  const { context } = plugin();
  let userRemoved = false;
  let connectorRemoved = false;
  const userVector = { name: "Vector → next", getPluginData: () => "", remove: () => { userRemoved = true; } };
  const connector = { name: "V1: A → B", getPluginData: () => "connector", remove: () => { connectorRemoved = true; } };
  context.figma.currentPage.findAll = (predicate) => [userVector, connector].filter(predicate);
  await context.figma.ui.onmessage({ type: "FINISH_SELECTION" });
  assert.equal(userRemoved, false);
  assert.equal(connectorRemoved, true);
});

test("bundled plugin scripts parse and charts have no CDN dependency", () => {
  const { execFileSync } = require("node:child_process");
  execFileSync(process.execPath, ["build.mjs"]);
  const html = fs.readFileSync("dist/ui.html", "utf8");
  assert.equal(html.includes('src="https://cdn.jsdelivr.net/npm/chart.js"'), false);
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  assert.equal(scripts.length, 2);
  scripts.forEach((script) => assert.doesNotThrow(() => new vm.Script(script[1])));
});

test("result warning identifies failed and timed-out clicks while retaining charts", () => {
  const html = fs.readFileSync("ui.html", "utf8");
  const code = html.slice(html.indexOf("function outcomeDescription(summary)"), html.indexOf("// Build the Muscle Cost, Finger Trajectory"));
  const warning = {};
  const context = vm.createContext({
    document: { getElementById: () => warning }, selectedElements: [{name: "First"}, {name: "Second"}],
    result: { outcome_summary: {status: "partial_failure",total:2,successful:1,failed:1,timed_out:1,unknown:0},
      episodes: [{outcome:{is_success:true}}, {data:{trajectory:[[1,2,3]]},outcome:{is_success:false,truncated:true}}] },
  });
  vm.runInContext(code + ";renderOutcomeWarning(result)", context);
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /1 of 2 gestures did not complete successfully/);
  assert.match(warning.textContent, /Second \(timed out\)/);
  assert.equal(context.result.episodes[1].data.trajectory.length, 1);
  context.result.outcome_summary = {status:"success",total:2,successful:2,failed:0,timed_out:0,unknown:0};
  vm.runInContext("renderOutcomeWarning(result)", context);
  assert.equal(warning.hidden, true);
});

test("successful gestures outrank fast failed trajectories in run comparisons", () => {
  const html = fs.readFileSync("ui.html", "utf8");
  const code = html.slice(html.indexOf("function determineBetterRun(runA, runB)"), html.indexOf("// Render both runs selected elements lists"));
  const context = vm.createContext({getRunMetrics: (run) => run.metrics,
    a: {metrics:{avg_muscle_cost:0.01,total_steps:1,outcome_summary:{total:1,successful:0,unknown:0}}},
    b: {metrics:{avg_muscle_cost:1,total_steps:100,outcome_summary:{total:1,successful:1,unknown:0}}},
  });
  assert.equal(vm.runInContext(code + ";determineBetterRun(a,b)", context), "b");
});
