#!/usr/bin/env node
// Frontend test for the v3 COMPOSITING controls (blend mode + PiP / gradient /
// shape / crossfade actions) surfaced in the clip inspector.
//
// Strategy (no browser, mirrors v3_inspector_test.mjs / v3_keyframe_editor_test.mjs):
// load the REAL static/v3/v3.js inside a node `vm` context backed by a minimal
// DOM shim, then drive the public debug/test hook window.__lumeriCompositing and
// the inspector controls, asserting the EXACT op structure each emits down the
// existing postTimelineOp path (no invented backend endpoint):
//
//   - blendOptions() returns the 14 backend blend modes
//   - emitSetBlend('c1','screen') -> {op:'set_blend', layer_id:'c1', mode:'screen'}
//   - emitPip('c1')               -> {op:'pip', layer_id:'c1', ...sensible defaults}
//   - emitAddGradient()           -> {op:'add_gradient', mode:'linear', stops:[2]}
//   - emitAddShape()              -> {op:'add_shape', kind:'rect', ...}
//   - emitCrossfade('a','b')      -> {op:'crossfade', from_id:'a', to_id:'b', ...}
//   - lastOp() tracks the most recent emitted op
//   - the inspector renders a blend <select> with the 14 modes that emits set_blend,
//     and Make PiP / Add gradient / Add shape / Crossfade buttons that emit their ops
//   - every emitted op also reaches the real /timeline/op fetch path (opCalls)
//
// Run: node tests/v3_compositing_ui_test.mjs   (prints PASS / exits 0 on success)

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const V3_JS = path.resolve(__dirname, "../static/v3/v3.js");

// ── tiny assert ──────────────────────────────────────────────────────
let checks = 0;
function assert(cond, msg) {
  checks += 1;
  if (!cond) {
    console.error(`FAIL: ${msg}`);
    process.exit(1);
  }
}
function assertEq(actual, expected, msg) {
  assert(actual === expected, `${msg} (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`);
}
function assertClose(actual, expected, msg, eps = 1e-9) {
  assert(Math.abs(actual - expected) <= eps, `${msg} (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`);
}
function assertDeep(actual, expected, msg) {
  assert(JSON.stringify(actual) === JSON.stringify(expected), `${msg} (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`);
}

// ── minimal DOM shim (same shape as the prior inspector/keyframe tests) ──
const byId = new Map();

class ClassList {
  constructor(el) { this.el = el; this._set = new Set(); }
  add(...cs) { for (const c of cs) this._set.add(c); this.el._syncClassName(); }
  remove(...cs) { for (const c of cs) this._set.delete(c); this.el._syncClassName(); }
  toggle(c, force) {
    const want = force === undefined ? !this._set.has(c) : !!force;
    if (want) this._set.add(c); else this._set.delete(c);
    this.el._syncClassName();
    return want;
  }
  contains(c) { return this._set.has(c); }
}

class Element {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._attrs = {};
    this.dataset = {};
    this.style = {};
    this._listeners = {};
    this._className = "";
    this._id = "";
    this._textContent = "";
    this._innerHTML = "";
    this.classList = new ClassList(this);
    this.hidden = false;
    this.disabled = false;
    this.selected = false;
    this.value = "";
  }

  _syncClassName() { this._className = [...this.classList._set].join(" "); }
  set className(v) {
    this._className = String(v || "");
    this.classList._set = new Set(this._className.split(/\s+/).filter(Boolean));
  }
  get className() { return this._className; }

  set id(v) {
    if (this._id) byId.delete(this._id);
    this._id = String(v || "");
    if (this._id) byId.set(this._id, this);
  }
  get id() { return this._id; }

  set textContent(v) { this._textContent = v == null ? "" : String(v); }
  get textContent() { return this._textContent; }

  set innerHTML(v) {
    this._innerHTML = String(v == null ? "" : v);
    for (const c of this.children) c.parentNode = null;
    this.children = [];
  }
  get innerHTML() { return this._innerHTML; }

  setAttribute(k, v) {
    this._attrs[k] = String(v);
    if (k === "id") this.id = v;
    if (k === "class") this.className = v;
    if (k && k.startsWith("data-")) {
      const key = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[key] = String(v);
    }
  }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }

  appendChild(node) {
    if (!node) return node;
    node.parentNode = this;
    this.children.push(node);
    return node;
  }
  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i >= 0) { this.children.splice(i, 1); node.parentNode = null; }
    return node;
  }
  insertBefore(node, ref) {
    node.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(node);
    else this.children.splice(i, 0, node);
    return node;
  }
  get nextSibling() {
    if (!this.parentNode) return null;
    const sibs = this.parentNode.children;
    const i = sibs.indexOf(this);
    return i >= 0 && i + 1 < sibs.length ? sibs[i + 1] : null;
  }

  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const arr = this._listeners[type];
    if (arr) this._listeners[type] = arr.filter((f) => f !== fn);
  }
  dispatch(type, ev) { for (const fn of this._listeners[type] || []) fn(ev || {}); }

  setPointerCapture() {}
  releasePointerCapture() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; }

  _walk(pred, out) {
    for (const c of this.children) {
      if (pred(c)) out.push(c);
      c._walk(pred, out);
    }
    return out;
  }
  _matches(sel) {
    sel = sel.trim();
    if (sel.startsWith("#")) return this.id === sel.slice(1);
    if (sel.startsWith(".")) return this.classList.contains(sel.slice(1));
    if (sel.startsWith("[") && sel.endsWith("]")) {
      const body = sel.slice(1, -1);
      const eq = body.indexOf("=");
      if (eq < 0) return body in this._attrs;
      const key = body.slice(0, eq);
      let want = body.slice(eq + 1);
      if ((want.startsWith('"') && want.endsWith('"')) || (want.startsWith("'") && want.endsWith("'"))) {
        want = want.slice(1, -1);
      }
      return this._attrs[key] === want;
    }
    return this.tagName === sel.toUpperCase();
  }
  querySelectorAll(sel) {
    const token = sel.trim().split(/\s+/).pop();
    return this._walk((c) => c._matches(token), []);
  }
  querySelector(sel) {
    const all = this.querySelectorAll(sel);
    return all.length ? all[0] : null;
  }
  closest(sel) {
    let n = this;
    while (n) {
      if (n._matches && n._matches(sel)) return n;
      n = n.parentNode;
    }
    return null;
  }
}

function createElement(tag) { return new Element(tag); }

const documentEl = new Element("html");
const body = new Element("body");
documentEl.appendChild(body);

const document = {
  _root: documentEl,
  body,
  createElement,
  getElementById: (id) => byId.get(id) || null,
  querySelector: (sel) => {
    if (sel.startsWith("#")) return byId.get(sel.slice(1)) || null;
    return documentEl.querySelector(sel);
  },
  querySelectorAll: (sel) => documentEl.querySelectorAll(sel),
  addEventListener: () => {},
  removeEventListener: () => {},
};

const BOOT_IDS = [
  "session-id-label", "connection-pill", "new-session-btn", "timeline",
  "empty-state", "asset-grid", "upload-input", "upload-btn", "prompt-input",
  "send-btn", "sandbox-toggle-btn",
  "project-timeline-panel", "project-timeline-tracks", "project-timeline-meta",
  "pt-edit-hint", "pt-split-btn", "pt-delete-btn", "pt-undo-btn",
];
for (const id of BOOT_IDS) {
  const node = createElement("div");
  node.id = id;
  body.appendChild(node);
}

// ── globals: fetch / EventSource / storage / timers / navigator ──────
// Capture every timeline op by recording the POST to /timeline/op.
const opCalls = [];
function fetchStub(url, init) {
  if (typeof url === "string" && /\/sessions\/?$/.test(url) && init && init.method === "POST") {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ session_id: "test-session" }),
    });
  }
  if (typeof url === "string" && url.includes("/timeline/op")) {
    let body = {};
    try { body = JSON.parse(init && init.body); } catch {}
    opCalls.push(body);
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ tracks: [], duration: 0 }),
    });
  }
  return Promise.reject(new Error("no network in test"));
}

class EventSourceStub { constructor() { this.onopen = null; this.onerror = null; this.onmessage = null; } close() {} }
const localStorageStub = {
  _m: new Map(),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, String(v)); },
  removeItem(k) { this._m.delete(k); },
};

const windowObj = {
  document, localStorage: localStorageStub, fetch: fetchStub, EventSource: EventSourceStub,
  navigator: { sendBeacon: () => true }, console,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
};
windowObj.window = windowObj;

const sandbox = {
  window: windowObj, document, localStorage: localStorageStub, fetch: fetchStub,
  EventSource: EventSourceStub, navigator: windowObj.navigator, console,
  setTimeout: windowObj.setTimeout, clearTimeout: windowObj.clearTimeout,
  setInterval: windowObj.setInterval, clearInterval: windowObj.clearInterval,
};

// ── load the real v3.js into the vm context ──────────────────────────
const src = fs.readFileSync(V3_JS, "utf8");
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "v3.js" });

const C = windowObj.__lumeriCompositing;
assert(C && typeof C.blendOptions === "function", "window.__lumeriCompositing.blendOptions is exposed");
assert(typeof C.emitSetBlend === "function", "emitSetBlend is exposed");
assert(typeof C.emitPip === "function", "emitPip is exposed");
assert(typeof C.emitAddGradient === "function", "emitAddGradient is exposed");
assert(typeof C.emitAddShape === "function", "emitAddShape is exposed");
assert(typeof C.emitCrossfade === "function", "emitCrossfade is exposed");
assert(typeof C.lastOp === "function", "lastOp is exposed");

const INS = windowObj.__lumeriInspector;
assert(INS && typeof INS.build === "function", "window.__lumeriInspector.build is exposed (untouched)");

// Let boot's async createSession() settle so state.sessionId is set; the
// emitters fan out through postTimelineOp, which no-ops without a session.
for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));

// ── Test 1: blendOptions() returns the 14 backend blend modes ─────────
{
  const opts = C.blendOptions();
  assert(Array.isArray(opts), "blendOptions() returns an array");
  assertEq(opts.length, 14, "blendOptions() lists 14 modes");
  const want = [
    "normal", "multiply", "screen", "overlay", "add", "lighten", "darken",
    "soft_light", "hard_light", "difference", "exclusion",
    "color_dodge", "color_burn", "subtract",
  ];
  // every required mode present (set equality, order-independent for robustness)
  const set = new Set(opts);
  for (const m of want) assert(set.has(m), `blendOptions() includes ${m}`);
  assertEq(set.size, 14, "blendOptions() has no duplicates");
  // returns a copy (mutating the result must not corrupt internal state)
  opts.push("bogus");
  assertEq(C.blendOptions().length, 14, "blendOptions() returns a fresh copy each call");
  console.log(`  blendOptions: ${want.length} modes ✓`);
}

// ── Test 2: emitSetBlend produces {op:'set_blend', layer_id, mode} ────
{
  opCalls.length = 0;
  const r = C.emitSetBlend("c1", "screen");
  assert(r != null, "emitSetBlend returns the postTimelineOp promise");
  const op = C.lastOp();
  assertDeep(op, { op: "set_blend", layer_id: "c1", mode: "screen" }, "emitSetBlend op structure");
  // also reached the real op path
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "emitSetBlend dispatched exactly one timeline op");
  assertEq(opCalls[0].op, "set_blend", "dispatched op is set_blend");
  assertEq(opCalls[0].layer_id, "c1", "dispatched op layer_id == c1");
  assertEq(opCalls[0].mode, "screen", "dispatched op mode == screen");
  // a different valid mode
  C.emitSetBlend("c2", "color_dodge");
  assertEq(C.lastOp().mode, "color_dodge", "lastOp tracks mode color_dodge");
  assertEq(C.lastOp().layer_id, "c2", "lastOp tracks layer_id c2");
  console.log(`  emitSetBlend: ${JSON.stringify(op)}`);
}

// ── Test 3: emitPip produces {op:'pip', layer_id, ...defaults} ────────
{
  opCalls.length = 0;
  C.emitPip("c1");
  const op = C.lastOp();
  assertEq(op.op, "pip", "emitPip op == pip");
  assertEq(op.layer_id, "c1", "emitPip layer_id == c1");
  assertEq(op.corner, "br", "emitPip default corner br");
  assertClose(op.scale, 0.3, "emitPip default scale 0.3");
  assert(op.radius > 0, "emitPip ships a rounded radius (> 0)");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "emitPip dispatched one timeline op");
  assertEq(opCalls[0].op, "pip", "dispatched op is pip");
  // overrides honoured
  C.emitPip("c9", { corner: "tl", scale: 0.5 });
  assertEq(C.lastOp().corner, "tl", "emitPip honours corner override");
  assertClose(C.lastOp().scale, 0.5, "emitPip honours scale override");
  console.log(`  emitPip: ${JSON.stringify(op)}`);
}

// ── Test 4: emitAddGradient produces a default 2-stop linear gradient ─
{
  opCalls.length = 0;
  C.emitAddGradient();
  const op = C.lastOp();
  assertEq(op.op, "add_gradient", "emitAddGradient op == add_gradient");
  assertEq(op.mode, "linear", "emitAddGradient default mode linear");
  assert(Array.isArray(op.stops) && op.stops.length === 2, "emitAddGradient ships 2 stops");
  assertClose(op.stops[0][0], 0.0, "first stop position 0.0");
  assertClose(op.stops[1][0], 1.0, "last stop position 1.0");
  assertEq(typeof op.stops[0][1], "string", "stop colours are strings");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "emitAddGradient dispatched one timeline op");
  assertEq(opCalls[0].op, "add_gradient", "dispatched op is add_gradient");
  console.log(`  emitAddGradient: ${JSON.stringify(op)}`);
}

// ── Test 5: emitAddShape produces a rect shape ───────────────────────
{
  opCalls.length = 0;
  C.emitAddShape();
  const op = C.lastOp();
  assertEq(op.op, "add_shape", "emitAddShape op == add_shape");
  assertEq(op.kind, "rect", "emitAddShape kind rect");
  assert(Array.isArray(op.rect) && op.rect.length === 4, "emitAddShape ships a 4-tuple rect");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "emitAddShape dispatched one timeline op");
  assertEq(opCalls[0].op, "add_shape", "dispatched op is add_shape");
  assertEq(opCalls[0].kind, "rect", "dispatched shape kind rect");
  console.log(`  emitAddShape: ${JSON.stringify(op)}`);
}

// ── Test 6: emitCrossfade produces {op:'crossfade', from_id, to_id} ──
{
  opCalls.length = 0;
  C.emitCrossfade("a", "b");
  const op = C.lastOp();
  assertEq(op.op, "crossfade", "emitCrossfade op == crossfade");
  assertEq(op.from_id, "a", "emitCrossfade from_id == a");
  assertEq(op.to_id, "b", "emitCrossfade to_id == b");
  assert(op.duration > 0, "emitCrossfade ships a positive duration");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "emitCrossfade dispatched one timeline op");
  assertEq(opCalls[0].op, "crossfade", "dispatched op is crossfade");
  // guards: missing ids -> no op
  opCalls.length = 0;
  const before = C.lastOp();
  C.emitCrossfade("a", null);
  assert(C.lastOp() === before, "emitCrossfade with a missing id is a no-op");
  console.log(`  emitCrossfade: ${JSON.stringify(op)}`);
}

// ── Test 7: inspector renders a blend <select> with 14 modes + emits set_blend ──
{
  opCalls.length = 0;
  const container = createElement("div");
  const layer = { id: "clip-Z", name: "Hero", media_kind: "video", blend: "multiply", effects: { opacity: 1 } };
  INS.build(container, layer);

  const sel = container.querySelector(".inspector-blend");
  assert(sel, "inspector renders a blend-mode <select>");
  const optionEls = sel.querySelectorAll("option");
  assertEq(optionEls.length, 14, "blend <select> lists 14 options");
  assertEq(sel.value, "multiply", "blend <select> pre-selects the layer's blend (multiply)");

  // change the select -> emits set_blend through the existing op path
  sel.value = "overlay";
  sel.dispatch("change", {});
  const op = C.lastOp();
  assertDeep(op, { op: "set_blend", layer_id: "clip-Z", mode: "overlay" }, "blend <select> change emits set_blend op");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "blend <select> change dispatched exactly one op");
  assertEq(opCalls[0].op, "set_blend", "dispatched op is set_blend");
  assertEq(opCalls[0].mode, "overlay", "dispatched blend mode overlay");
  console.log(`  inspector blend <select>: 14 opts, change -> ${JSON.stringify(op)}`);
}

// ── Test 8: inspector buttons emit pip / gradient / shape ops ────────
{
  const container = createElement("div");
  const layer = { id: "clip-Y", name: "Sub", media_kind: "video", effects: { opacity: 1 } };
  INS.build(container, layer);

  const pipBtn = container.querySelector('[data-action="pip"]');
  const gradBtn = container.querySelector('[data-action="add-gradient"]');
  const shapeBtn = container.querySelector('[data-action="add-shape"]');
  assert(pipBtn, "inspector has a Make PiP button");
  assert(gradBtn, "inspector has an Add gradient button");
  assert(shapeBtn, "inspector has an Add shape button");
  assertEq(pipBtn.textContent, "Make PiP", "PiP button label");

  opCalls.length = 0;
  pipBtn.dispatch("click", {});
  assertEq(C.lastOp().op, "pip", "Make PiP button emits pip op");
  assertEq(C.lastOp().layer_id, "clip-Y", "PiP op targets the selected clip");

  gradBtn.dispatch("click", {});
  assertEq(C.lastOp().op, "add_gradient", "Add gradient button emits add_gradient op");

  shapeBtn.dispatch("click", {});
  assertEq(C.lastOp().op, "add_shape", "Add shape button emits add_shape op");

  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 3, "three inspector buttons dispatched three ops");
  console.log(`  inspector buttons: pip/add_gradient/add_shape all fire ✓`);
}

// ── Test 9: Crossfade button is disabled with <2 clips, fires with 2 ──
{
  // no timeline -> single candidate -> disabled
  const c1 = createElement("div");
  INS.build(c1, { id: "solo", name: "Solo", effects: {} });
  const x1 = c1.querySelector('[data-action="crossfade"]');
  assert(x1, "inspector has a Crossfade button");
  assertEq(x1.disabled, true, "Crossfade disabled when fewer than two clips are selectable");

  // populate a two-clip timeline + select the first, via the PUBLIC hooks
  // (no poking at the IIFE-private `state`), then rebuild the inspector.
  const TUI = windowObj.__lumeriTimelineUI;
  assert(TUI && typeof TUI.renderPanel === "function", "__lumeriTimelineUI.renderPanel is exposed");
  TUI.renderPanel({
    duration: 10, fps: 30,
    tracks: [{ clips: [{ id: "A", start: 0, duration: 5 }, { id: "B", start: 5, duration: 5 }] }],
  });
  TUI.selectClip("A");

  const c2 = createElement("div");
  INS.build(c2, { id: "A", name: "A", start: 0, duration: 5, effects: {} });
  const x2 = c2.querySelector('[data-action="crossfade"]');
  assertEq(!!x2.disabled, false, "Crossfade enabled with two selectable clips");

  opCalls.length = 0;
  x2.dispatch("click", {});
  const op = C.lastOp();
  assertEq(op.op, "crossfade", "Crossfade button emits crossfade op");
  assertEq(op.from_id, "A", "crossfade from selected clip A");
  assertEq(op.to_id, "B", "crossfade to the next clip B");
  await new Promise((r) => setImmediate(r));
  assertEq(opCalls.length, 1, "Crossfade button dispatched one op");
  console.log(`  inspector Crossfade: disabled@1 clip, fires A->B @2 clips: ${JSON.stringify(op)}`);
}

console.log(`PASS (${checks} checks)`);
process.exit(0);
