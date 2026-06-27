#!/usr/bin/env node
// Frontend test for the v3 keyframe-track editor strip (curve-editing basis).
//
// Strategy (no browser, mirrors v3_frame_ruler_test.mjs): load the REAL
// static/v3/v3.js inside a node `vm` context backed by a minimal DOM shim
// (createElement / appendChild / removeChild / insertBefore / classList /
// style / setAttribute / dataset / getElementById / querySelector*). Then
// drive the public debug/test hook window.__lumeriKeyframeEditor and assert
// COMPUTED values:
//
//   - marker count == keyframes.length
//   - marker left px == t * pxPerSec  (positions, including unsorted input)
//   - addKeyframe grows the marker count and keeps the list sorted by t
//   - moveKeyframe snaps t to the nearest frame (t = round(t*fps)/fps) and
//     re-sorts, returning the keyframe's new index
//
// Run: node tests/v3_keyframe_editor_test.mjs  (prints PASS / exits 0 on success)

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

// ── minimal DOM shim (same shape as the round-1 frame-ruler test) ────
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

function fetchStub() { return Promise.reject(new Error("no network in test")); }
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

const KE = windowObj.__lumeriKeyframeEditor;
assert(KE && typeof KE.build === "function", "window.__lumeriKeyframeEditor.build is exposed");
assert(typeof KE.addKeyframe === "function", "window.__lumeriKeyframeEditor.addKeyframe is exposed");
assert(typeof KE.moveKeyframe === "function", "window.__lumeriKeyframeEditor.moveKeyframe is exposed");
assert(typeof KE.markers === "function", "window.__lumeriKeyframeEditor.markers is exposed");

// ── helpers to read computed pixel values off the shim ───────────────
function pxToNum(s) { return Number(String(s).replace("px", "")); }
function markerEls(container) { return container.querySelectorAll(".kf-marker"); }
function markerLefts(container) { return markerEls(container).map((m) => pxToNum(m.style.left)); }
function markerTs(container) { return markerEls(container).map((m) => Number(m.getAttribute("data-t"))); }
function isSortedAsc(arr) { for (let i = 1; i < arr.length; i++) if (arr[i] < arr[i - 1]) return false; return true; }

// ── Test 1: marker count == keyframes.length; left px == t*pxPerSec ──
{
  const container = createElement("div");
  const pxPerSec = 80;
  const keyframes = [
    { t: 0, value: 0 },
    { t: 0.5, value: 50 },
    { t: 1.0, value: 100 },
    { t: 2.0, value: 0 },
  ];
  const inst = KE.build(container, { property: "opacity", keyframes, durationSec: 3, fps: 30, pxPerSec });
  assert(inst, "build returns an instance");

  assertEq(markerEls(container).length, keyframes.length, "marker count == keyframes.length");

  const lefts = markerLefts(container);
  keyframes.forEach((kf, i) => {
    assertClose(lefts[i], kf.t * pxPerSec, `marker[${i}] left px == t(${kf.t})*pxPerSec(${pxPerSec})`);
  });

  // markers() reports the same computed positions
  const mk = KE.markers();
  assertEq(mk.length, keyframes.length, "markers() length == keyframes.length");
  keyframes.forEach((kf, i) => assertClose(mk[i].left, kf.t * pxPerSec, `markers()[${i}].left == t*pxPerSec`));
  assertEq(inst.property, "opacity", "instance carries the property name");
  console.log(`  positions: pxPerSec=${pxPerSec}, lefts=[${lefts.join(", ")}] (expect [0, 40, 80, 160])`);
}

// ── Test 2: build sorts unsorted input by t ascending ────────────────
{
  const container = createElement("div");
  const pxPerSec = 100;
  const unsorted = [
    { t: 2.0, value: "c" },
    { t: 0.0, value: "a" },
    { t: 1.0, value: "b" },
  ];
  KE.build(container, { property: "scale", keyframes: unsorted, durationSec: 3, fps: 24, pxPerSec });
  const ts = markerTs(container);
  assert(isSortedAsc(ts), "build sorts markers ascending by t");
  assertEq(JSON.stringify(ts), JSON.stringify([0, 1, 2]), "sorted t order is [0,1,2]");
  // positions follow the sorted order
  assertEq(JSON.stringify(markerLefts(container)), JSON.stringify([0, 100, 200]), "sorted left px == [0,100,200]");
  console.log(`  sort: input t=[2,0,1] -> rendered t=[${ts.join(",")}]`);
}

// ── Test 3: addKeyframe grows count and keeps the list sorted ────────
{
  const container = createElement("div");
  const pxPerSec = 80;
  KE.build(container, { property: "x", keyframes: [{ t: 0, value: 0 }, { t: 2, value: 200 }], durationSec: 4, fps: 30, pxPerSec });
  assertEq(markerEls(container).length, 2, "starts with 2 markers");

  // insert in the middle
  const idxMid = KE.addKeyframe(1, 100);
  assertEq(markerEls(container).length, 3, "addKeyframe grows markers to 3");
  assertEq(idxMid, 1, "addKeyframe(1) returns the sorted insert index 1");
  assert(isSortedAsc(markerTs(container)), "list stays sorted after middle insert");
  assertEq(JSON.stringify(markerTs(container)), JSON.stringify([0, 1, 2]), "t order [0,1,2] after middle insert");

  // insert at the front
  const idxFront = KE.addKeyframe(0.5, 50);
  assertEq(markerEls(container).length, 4, "addKeyframe grows markers to 4");
  assert(isSortedAsc(markerTs(container)), "list stays sorted after front-ish insert");
  assertEq(JSON.stringify(markerTs(container)), JSON.stringify([0, 0.5, 1, 2]), "t order [0,0.5,1,2]");

  // new marker's left px reflects its t
  const lefts = markerLefts(container);
  assertClose(lefts[1], 0.5 * pxPerSec, "inserted marker left px == 0.5*pxPerSec (40)");
  console.log(`  add: 2 -> ${markerEls(container).length} markers, t=[${markerTs(container).join(",")}], idxMid=${idxMid}`);
}

// ── Test 4: moveKeyframe snaps t to the nearest frame (round(t*fps)/fps) ─
{
  const container = createElement("div");
  const pxPerSec = 90;
  const fps = 30;
  KE.build(container, { property: "rotation", keyframes: [{ t: 0, value: 0 }, { t: 1, value: 90 }], durationSec: 5, fps, pxPerSec });

  // move index 1 from t=1 to t=1.337 -> round(1.337*30)/30 = round(40.11)/30 = 40/30
  const newIdx = KE.moveKeyframe(1, 1.337);
  const expectedT = Math.round(1.337 * fps) / fps; // 40/30 = 1.3333...
  assertEq(newIdx, 1, "moveKeyframe returns the keyframe's new index (still 1)");
  const ts = markerTs(container);
  assertClose(ts[1], expectedT, "moved t snapped to nearest frame (40/30)");
  assertClose(pxToNum(markerEls(container)[1].style.left), expectedT * pxPerSec, "moved marker left px == snappedT*pxPerSec");

  // a value that snaps DOWN: t=2.01 with fps=30 -> round(60.3)/30 = 60/30 = 2.0
  KE.addKeyframe(2.01, 5);
  // find the index of the ~2.01 keyframe (it sorted to the end)
  const tsBefore = markerTs(container);
  const idx201 = tsBefore.length - 1;
  KE.moveKeyframe(idx201, 2.01);
  const snapped = Math.round(2.01 * fps) / fps; // 2.0
  assertClose(markerTs(container)[markerTs(container).length - 1], snapped, "t=2.01 snaps down to 2.0 at fps=30");

  // moving causes a re-sort: move the first keyframe (t=0) past the others
  const movedIdx = KE.moveKeyframe(0, 3.0);
  assert(isSortedAsc(markerTs(container)), "list re-sorts after a move that reorders");
  assert(movedIdx >= 0, "moveKeyframe returns a valid new index after re-sort");

  // out-of-range index is a no-op returning -1
  assertEq(KE.moveKeyframe(999, 1.0), -1, "moveKeyframe with bad index returns -1");
  console.log(`  move: 1.337s -> ${expectedT.toFixed(4)}s (frame ${Math.round(1.337 * fps)}), 2.01s -> ${snapped}s`);
}

// ── Test 5: panel-style build attaches a real subtree; rebuild replaces ─
{
  const host = createElement("div");
  KE.build(host, { property: "value", keyframes: [{ t: 0, value: 0 }, { t: 1, value: 1 }], durationSec: 2, fps: 30, pxPerSec: 180 });
  assert(host.querySelector(".kf-track") != null, "build attaches a .kf-track to the container");
  assert(host.querySelector(".kf-track-lane") != null, "build attaches a .kf-track-lane");
  assertEq(host.querySelectorAll(".kf-marker").length, 2, "panel-style build: 2 markers");
  // track width spans the duration
  const track = host.querySelector(".kf-track");
  assertEq(pxToNum(track.style.width), 2 * 180, "track width px == durationSec*pxPerSec");

  // Rebuild into the same host (like the panel does on render): replace, not stack
  host.innerHTML = "";
  KE.build(host, { property: "value", keyframes: [{ t: 0.25, value: 9 }], durationSec: 1, fps: 30, pxPerSec: 80 });
  assertEq(host.querySelectorAll(".kf-marker").length, 1, "rebuild into same host: 1 marker, no leftovers");
  assertClose(pxToNum(host.querySelector(".kf-marker").style.left), 0.25 * 80, "rebuilt marker left px == 0.25*80 (20)");
  console.log(`  panel-build: subtree OK, rebuild replaces (1 marker @ 20px)`);
}

console.log(`PASS (${checks} checks)`);
process.exit(0);
