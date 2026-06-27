#!/usr/bin/env node
// Frontend test for the v3 clip/layer INSPECTOR panel.
//
// Strategy (no browser, mirrors v3_frame_ruler_test.mjs / v3_keyframe_editor_test.mjs):
// load the REAL static/v3/v3.js inside a node `vm` context backed by a minimal
// DOM shim (createElement / appendChild / removeChild / insertBefore / classList /
// style / setAttribute / dataset / getElementById / querySelector* / addEventListener
// + dispatch). Then drive the public debug/test hook window.__lumeriInspector and
// assert COMPUTED values:
//
//   - the inspector shows the selected layer's transform (x/y/scale/rotation),
//     opacity, and blend with their actual values
//   - the opacity slider's value reflects layer.effects.opacity
//   - readControls() returns the panel's current (post-edit) values
//   - editing the opacity slider (input/change) updates readControls() and the
//     rendered output, and emits the existing set_effects op (no invented endpoint)
//   - the reset button restores opacity to 1
//   - selecting a different layer (rebuild into the same host) updates the panel
//
// Run: node tests/v3_inspector_test.mjs   (prints PASS / exits 0 on success)

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

// ── minimal DOM shim (same shape as the prior frame-ruler/keyframe tests) ──
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
// We capture every postTimelineOp call by recording the fetch to /timeline/op,
// then resolving it with a benign body (so postTimelineOp's await chain settles
// without touching the panel render in a way the shim can't model).
const opCalls = [];
function fetchStub(url, init) {
  // Boot's createSession() POSTs /sessions; resolve it so state.sessionId is set
  // (postTimelineOp short-circuits without a session). Match the exact path so we
  // don't accidentally swallow the per-session sub-routes (/sessions/<id>/…).
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
    // Resolve with ok:true + an empty timeline so renderProjectTimeline is a no-op-ish.
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

const INS = windowObj.__lumeriInspector;
assert(INS && typeof INS.build === "function", "window.__lumeriInspector.build is exposed");
assert(typeof INS.readControls === "function", "window.__lumeriInspector.readControls is exposed");

// Let boot's async createSession() settle so state.sessionId is set; the
// opacity slider/reset controls emit through postTimelineOp, which no-ops
// without a session. A couple of microtask/macrotask turns is enough.
for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));

// ── helpers to read computed values off the shim ─────────────────────
function rowMap(container) {
  // { rowKeyText: rowValText } across all .inspector-row in the container
  const map = {};
  for (const row of container.querySelectorAll(".inspector-row")) {
    const k = row.querySelector(".inspector-key");
    const v = row.querySelector(".inspector-val");
    if (k && v) map[k.textContent] = v.textContent;
  }
  return map;
}
function slider(container) { return container.querySelector(".inspector-opacity"); }
function sliderOut(container) { return container.querySelector(".inspector-opacity-val"); }
function resetBtn(container) { return container.querySelector(".inspector-btn"); }

// ── Test 1: inspector shows the selected layer's transform/opacity/blend values ──
{
  const container = createElement("div");
  const layer = {
    id: "clip-A", name: "Intro", media_kind: "video", blend: "screen",
    effects: { x: 120, y: -40, scale: 1.5, rotation: 90, opacity: 0.42, blur_radius: 4 },
  };
  const inst = INS.build(container, layer);
  assert(inst, "build returns an instance");

  const rows = rowMap(container);
  assertEq(rows["X"], "120", "inspector X row shows effects.x (120)");
  assertEq(rows["Y"], "-40", "inspector Y row shows effects.y (-40)");
  assertEq(rows["Scale"], "1.5", "inspector Scale row shows effects.scale (1.5)");
  assertEq(rows["Rotation"], "90°", "inspector Rotation row shows effects.rotation (90°)");
  assertEq(rows["Blend"], "screen", "inspector Blend row shows layer.blend (screen)");
  // extra effect beyond the named transform set appears in the effects section
  assertEq(rows["blur_radius"], "4", "inspector lists extra effect blur_radius (4)");

  // opacity slider reflects layer.effects.opacity exactly
  const s = slider(container);
  assert(s, "inspector has an opacity slider");
  assertClose(Number(s.value), 0.42, "opacity slider value == layer.effects.opacity (0.42)");
  assertEq(sliderOut(container).textContent, "0.42", "opacity output text == 0.42");

  // readControls reflects the same computed values
  const rc = INS.readControls();
  assertClose(rc.opacity, 0.42, "readControls().opacity == 0.42");
  assertClose(rc.transform.x, 120, "readControls().transform.x == 120");
  assertClose(rc.transform.scale, 1.5, "readControls().transform.scale == 1.5");
  assertClose(rc.transform.rotation, 90, "readControls().transform.rotation == 90");
  assertEq(rc.blend, "screen", "readControls().blend == screen");
  console.log(`  layer A: x=${rows["X"]} y=${rows["Y"]} scale=${rows["Scale"]} rot=${rows["Rotation"]} opacity=${s.value} blend=${rc.blend}`);
}

// ── Test 2: defaults when the layer has no effects map ────────────────
{
  const container = createElement("div");
  const layer = { id: "clip-bare", name: "Bare", media_kind: "image" };
  INS.build(container, layer);
  const rows = rowMap(container);
  assertEq(rows["X"], "0", "default X == 0");
  assertEq(rows["Y"], "0", "default Y == 0");
  assertEq(rows["Scale"], "1", "default Scale == 1");
  assertEq(rows["Rotation"], "0°", "default Rotation == 0°");
  assertEq(rows["Blend"], "normal", "default Blend == normal");
  assertClose(Number(slider(container).value), 1, "default opacity slider == 1");
  // effects section shows 'none'
  assert(container.querySelector(".inspector-empty") != null, "no extra effects -> shows 'none'");
  console.log(`  bare layer: defaults x/y=0 scale=1 rot=0 opacity=1 blend=normal`);
}

// ── Test 3: editing the opacity slider updates readControls + emits set_effects ──
{
  opCalls.length = 0;
  const container = createElement("div");
  const layer = { id: "clip-B", name: "Mid", media_kind: "video", effects: { opacity: 0.8 } };
  INS.build(container, layer);
  const s = slider(container);
  assertClose(Number(s.value), 0.8, "slider starts at layer opacity 0.8");

  // user drags the slider to 0.25 (input fires live, output updates)
  s.value = "0.25";
  s.dispatch("input", {});
  assertEq(sliderOut(container).textContent, "0.25", "output reflects dragged value 0.25");
  assertClose(INS.readControls().opacity, 0.25, "readControls().opacity == edited 0.25");

  // releasing (change) commits through the existing op path
  s.dispatch("change", {});
  assertEq(opCalls.length, 1, "slider commit emits exactly one timeline op");
  assertEq(opCalls[0].op, "set_effects", "emitted op is the existing set_effects path");
  assertEq(opCalls[0].clip_id, "clip-B", "op carries the selected clip_id");
  assertClose(opCalls[0].effects.opacity, 0.25, "op effects.opacity == 0.25");
  console.log(`  edit: slider 0.8 -> 0.25, op=${opCalls[0].op} clip=${opCalls[0].clip_id} opacity=${opCalls[0].effects.opacity}`);
}

// ── Test 4: reset button restores opacity to 1 and emits set_effects ──
{
  opCalls.length = 0;
  const container = createElement("div");
  const layer = { id: "clip-C", name: "End", media_kind: "video", effects: { opacity: 0.1 } };
  INS.build(container, layer);
  const s = slider(container);
  assertClose(Number(s.value), 0.1, "slider starts at 0.1");

  resetBtn(container).dispatch("click", {});
  assertClose(Number(s.value), 1, "reset sets slider value to 1");
  assertEq(sliderOut(container).textContent, "1", "reset sets output to 1");
  assertClose(INS.readControls().opacity, 1, "readControls().opacity == 1 after reset");
  assertEq(opCalls.length, 1, "reset emits exactly one op");
  assertEq(opCalls[0].op, "set_effects", "reset op is set_effects");
  assertClose(opCalls[0].effects.opacity, 1, "reset op effects.opacity == 1");
  console.log(`  reset: 0.1 -> 1, op=${opCalls[0].op} opacity=${opCalls[0].effects.opacity}`);
}

// ── Test 5: opacity is clamped to the backend [0,1] domain ───────────
{
  opCalls.length = 0;
  const container = createElement("div");
  const layer = { id: "clip-clamp", name: "Clamp", effects: { opacity: 5 } };
  INS.build(container, layer);
  // an out-of-range stored opacity is clamped on display
  assertClose(Number(slider(container).value), 1, "stored opacity 5 clamps to 1 on the slider");
  assertClose(INS.readControls().opacity, 1, "readControls clamps to 1");

  // dragging below 0 clamps to 0 in the emitted op
  const s = slider(container);
  s.value = "-0.5";
  s.dispatch("change", {});
  assertClose(opCalls[0].effects.opacity, 0, "negative drag clamps emitted opacity to 0");
  console.log(`  clamp: stored 5 -> 1, drag -0.5 -> emitted ${opCalls[0].effects.opacity}`);
}

// ── Test 6: selecting a different layer updates the panel (rebuild into host) ──
{
  const host = createElement("div");
  const layerA = { id: "L1", name: "First", media_kind: "video", blend: "normal", effects: { scale: 2, opacity: 0.9 } };
  INS.build(host, layerA);
  let rows = rowMap(host);
  assertEq(rows["Scale"], "2", "panel shows layer A scale (2)");
  assertClose(Number(slider(host).value), 0.9, "panel shows layer A opacity (0.9)");
  assertEq(host.querySelectorAll(".inspector").length, 1, "one inspector after first build");

  // re-render the same host with a different layer, exactly like renderInspector
  host.innerHTML = "";
  const layerB = { id: "L2", name: "Second", media_kind: "image", blend: "multiply", effects: { scale: 0.5, rotation: 180, opacity: 0.3 } };
  INS.build(host, layerB);
  rows = rowMap(host);
  assertEq(host.querySelectorAll(".inspector").length, 1, "still one inspector after reselect (no leftovers)");
  assertEq(rows["Scale"], "0.5", "panel updates to layer B scale (0.5)");
  assertEq(rows["Rotation"], "180°", "panel updates to layer B rotation (180°)");
  assertEq(rows["Blend"], "multiply", "panel updates to layer B blend (multiply)");
  assertClose(Number(slider(host).value), 0.3, "opacity slider updates to layer B (0.3)");
  assertClose(INS.readControls().opacity, 0.3, "readControls() follows the newly selected layer (0.3)");
  // the active instance points at layer B
  assertEq(host.querySelector(".inspector").getAttribute("data-clip-id"), "L2", "active inspector is bound to layer B id");
  console.log(`  reselect: L1(scale 2, op 0.9) -> L2(scale ${rows["Scale"]}, rot ${rows["Rotation"]}, op ${slider(host).value})`);
}

console.log(`PASS (${checks} checks)`);
process.exit(0);
