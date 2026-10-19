const fs = require("node:fs");
const vm = require("node:vm");

function ui() {
  const elements = new Map();
  const timers = new Map();
  const sockets = [];
  const charts = [];
  const images = [];
  const alerts = [];
  let nextTimer = 0;
  let context;
  function element(id = "") {
    const classes = new Set();
    const listeners = new Map();
    const nested = new Map();
    const item = {
      id, children: [], dataset: {}, textContent: "", innerHTML: "", disabled: false,
      style: { display: "none", setProperty(name, value) { this[name] = value; } },
      classList: { add: (...names) => names.forEach((name) => classes.add(name)),
        remove: (...names) => names.forEach((name) => classes.delete(name)),
        contains: (name) => classes.has(name),
        toggle(name, force = !classes.has(name)) { if (force) classes.add(name); else classes.delete(name); } },
      addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) || []), listener]); },
      async dispatch(type, event = {}) { for (const listener of listeners.get(type) || []) await listener(event); },
      appendChild(child) { this.children.push(child); child.parentElement = this; },
      querySelector(selector) { if (!nested.has(selector)) nested.set(selector, element(selector)); return nested.get(selector); },
      querySelectorAll() { return []; },
      setAttribute(name, value) { this[name] = value; }, getAttribute(name) { return this[name]; },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 450, height: 300 }),
      play: () => Promise.resolve(), pause() { this.paused = true; }, load() {}, focus() {}, remove() {},
    };
    return item;
  }
  const document = {
    getElementById(id) {
      if (!elements.has(id)) { const item = element(id); elements.set(id, item); if (context) context[id] = item; }
      return elements.get(id);
    },
    querySelector: () => null, querySelectorAll: () => [], createElement: () => element(), addEventListener() {},
  };
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 1; this.sent = []; sockets.push(this); }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    receive(payload) { this.onmessage({ data: JSON.stringify(payload) }); }
  }
  class Chart {
    constructor(canvas, config) {
      this.id = charts.length + 1; this.canvas = canvas; this.options = config.options;
      this.data = config.data; this.config = config; this.draws = 0; this.visibility = new Map(); charts.push(this);
    }
    resize() {} render() {} update() {} draw() { this.draws++; }
    destroy() { this.canvas = null; this.destroyed = true; }
    isDatasetVisible(index) { return this.visibility.get(index) !== false; }
    setDatasetVisibility(index, visible) { this.visibility.set(index, visible); }
    toBase64Image() { if (this.destroyed) throw new Error("Cannot snapshot destroyed chart"); return `image-${this.id}`; }
  }
  context = vm.createContext({ document, console, Chart, WebSocket: Socket,
    Image: class { constructor() { images.push(this); } },
    MutationObserver: class { observe() {} },
    getComputedStyle: (item) => ({ ...item.style, marginBottom: "0", marginTop: "0" }),
    setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, {fn, ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    alert: (message) => alerts.push(message),
    parent: { postMessage() {} },
  });
  context.window = context;
  const html = fs.readFileSync("ui.html", "utf8");
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) document.getElementById(match[1]);
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  vm.runInContext(scripts.at(-1)[1], context);
  function run(code) { return vm.runInContext(code, context); }
  async function tick() { for (let i = 0; i < 6; i++) await Promise.resolve(); }
  async function flushTimers(maxMs = Infinity) {
    for (const [id, timer] of [...timers]) if (timer.ms <= maxMs) { timers.delete(id); await timer.fn(); }
    await tick();
  }
  return { context, document, elements, sockets, charts, images, alerts, run, tick, flushTimers };
}

function simulation(runId = "1", sessionId = "1") {
  return { run_id: runId, session_id: sessionId,
    outcome_summary: {status: "success", total: 1, successful: 1, failed: 0, unknown: 0},
    episodes: [{ episode_id: "episode_0", outcome: {is_success: true},
      data: {start_time:0, dt:0.01, trajectory:[[0,0,0],[1,1,1]], muscle_costs:[0.1,0.2], contact_indices:[1]} }] };
}

module.exports = { ui, simulation };
