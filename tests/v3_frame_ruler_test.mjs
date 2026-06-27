#!/usr/bin/env node
// Frontend test for the v3 frame ruler / playhead / scrubber / frame-step.
//
// Strategy (no browser): load the REAL static/v3/v3.js inside a node `vm`
// context backed by a minimal DOM shim (createElement / appendChild /
// removeChild / insertBefore / addEventListener / classList / style /
// setAttribute / dataset / getElementById / querySelector*). Then drive the
// public debug/test hook window.__lumeriFrameRuler and assert COMPUTED values:
//
//   - tick count == round(durationSec * fps) for a known duration/fps
//   - playhead left px == frame * pxPerFrame after seekToFrame(k)
//   - scrubber: a synthetic pointerdown at offsetX snaps to round(x/pxPerFrame)
//   - step(+1)/step(-1) move the frame by exactly 1, clamped at [0, total-1]
//
// Run: node tests/v3_frame_ruler_test.mjs   (prints PASS / exits 0 on success)

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

// ── minimal DOM shim ─────────────────────────────────────────────────
// One Element class covers all needs. getElementById/querySelector resolve
// against a live registry so dynamically created+inserted nodes are findable.

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
    this.scrolling = false;
  }

  // className kept in sync with classList and id registry
  _syncClassName() {
    const fromList = [...this.classList._set].join(" ");
    this._className = fromList;
  }
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
    // clearing children mirrors real innerHTML="" used by renderProjectTimeline
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

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const arr = this._listeners[type];
    if (arr) this._listeners[type] = arr.filter((f) => f !== fn);
  }
  // test helper: synchronously fire listeners
  dispatch(type, ev) {
    for (const fn of this._listeners[type] || []) fn(ev || {});
  }

  setPointerCapture() {}
  releasePointerCapture() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; }

  // descendant queries over the live tree
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
    // support simple "#id .class" / ".class" / "tag" forms; take last token
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

// document + body
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

// Pre-create the elements v3.js looks up at boot (mirrors index.html ids).
const BOOT_IDS = [
  "session-id-label", "connection-pill", "new-session-btn", "timeline",
  "empty-state", "asset-grid", "upload-input", "upload-btn", "prompt-input",
  "send-btn", "sandbox-toggle-btn",
  // project-timeline panel
  "project-timeline-panel", "project-timeline-tracks", "project-timeline-meta",
  "pt-edit-hint", "pt-split-btn", "pt-delete-btn", "pt-undo-btn",
];
for (const id of BOOT_IDS) {
  const node = createElement("div");
  node.id = id;
  body.appendChild(node);
}

// ── globals: fetch / EventSource / storage / timers / navigator ──────
// fetch always rejects → every caller in v3.js has .catch/try-catch, so boot is
// harmless and no network is touched.
function fetchStub() { return Promise.reject(new Error("no network in test")); }

class EventSourceStub {
  constructor() { this.onopen = null; this.onerror = null; this.onmessage = null; }
  close() {}
}

const localStorageStub = {
  _m: new Map(),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, String(v)); },
  removeItem(k) { this._m.delete(k); },
};

const windowObj = {
  document,
  localStorage: localStorageStub,
  fetch: fetchStub,
  EventSource: EventSourceStub,
  navigator: { sendBeacon: () => true },
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
};
windowObj.window = windowObj;

const sandbox = {
  window: windowObj,
  document,
  localStorage: localStorageStub,
  fetch: fetchStub,
  EventSource: EventSourceStub,
  navigator: windowObj.navigator,
  console,
  setTimeout: windowObj.setTimeout,
  clearTimeout: windowObj.clearTimeout,
  setInterval: windowObj.setInterval,
  clearInterval: windowObj.clearInterval,
};

// ── load the real v3.js into the vm context ──────────────────────────
const src = fs.readFileSync(V3_JS, "utf8");
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "v3.js" });

const FR = windowObj.__lumeriFrameRuler;
assert(FR && typeof FR.build === "function", "window.__lumeriFrameRuler.build is exposed");
assert(typeof FR.seekToFrame === "function", "window.__lumeriFrameRuler.seekToFrame is exposed");
assert(typeof FR.step === "function", "window.__lumeriFrameRuler.step is exposed");

// ── helpers to read computed pixel values off the shim ───────────────
function pxToNum(s) { return Number(String(s).replace("px", "")); }
function findRuler(container) { return container.querySelector(".frame-ruler"); }
function findPlayhead(container) { return container.querySelector(".playhead"); }
function tickCount(container) { return container.querySelectorAll(".frame-tick").length; }
function majorLabels(container) {
  return container.querySelectorAll(".frame-tick-label").map((n) => n.textContent);
}

// ── Test 1: tick count == round(durationSec * fps) ───────────────────
{
  const container = createElement("div");
  const durationSec = 2;
  const fps = 30;
  const pxPerFrame = 6;
  const inst = FR.build(container, { durationSec, fps, pxPerFrame, currentFrame: 0 });
  const totalFrames = Math.round(durationSec * fps); // 60
  assert(inst, "build returns an instance");
  assertEq(inst.totalFrames, totalFrames, "instance.totalFrames == round(dur*fps)");
  assertEq(tickCount(container), totalFrames, "rendered .frame-tick count == totalFrames");

  // ruler width == totalFrames * pxPerFrame
  const ruler = findRuler(container);
  assertEq(pxToNum(ruler.style.width), totalFrames * pxPerFrame, "ruler width px == totalFrames*pxPerFrame");

  // frame 0 is always a labeled (major) tick
  const labels = majorLabels(container);
  assert(labels.includes("0"), "frame 0 is a labeled major tick");
  console.log(`  tick-count: dur=${durationSec}s fps=${fps} -> totalFrames=${totalFrames}, ticks=${tickCount(container)}, majors=${labels.length}`);
}

// ── Test 2: playhead left px == frame * pxPerFrame after seekToFrame ──
{
  const container = createElement("div");
  const durationSec = 4;
  const fps = 25;            // totalFrames = 100
  const pxPerFrame = 8;
  FR.build(container, { durationSec, fps, pxPerFrame, currentFrame: 0 });
  const ph = findPlayhead(container);

  assertEq(pxToNum(ph.style.left), 0, "playhead left starts at 0px");

  for (const k of [0, 1, 7, 42, 99]) {
    const ret = FR.seekToFrame(k);
    assertEq(ret, k, `seekToFrame(${k}) returns ${k}`);
    assertEq(pxToNum(ph.style.left), k * pxPerFrame, `playhead left px == ${k}*${pxPerFrame}`);
    if (k === 42) {
      console.log(`  playhead: pxPerFrame=${pxPerFrame}, seek(42) -> left=${ph.style.left} (expect ${42 * pxPerFrame}px)`);
    }
  }

  // clamp on seek beyond bounds
  const total = Math.round(durationSec * fps); // 100
  assertEq(FR.seekToFrame(10_000), total - 1, "seek beyond end clamps to totalFrames-1");
  assertEq(pxToNum(ph.style.left), (total - 1) * pxPerFrame, "playhead clamps left px at end");
  assertEq(FR.seekToFrame(-50), 0, "seek below 0 clamps to 0");
  assertEq(pxToNum(ph.style.left), 0, "playhead clamps left px at 0");
}

// ── Test 3: scrubber pointerdown snaps to round(offsetX / pxPerFrame) ─
{
  const container = createElement("div");
  const pxPerFrame = 10;
  FR.build(container, { durationSec: 3, fps: 30, pxPerFrame, currentFrame: 0 }); // totalFrames=90
  const ruler = findRuler(container);
  const ph = findPlayhead(container);

  // offsetX 47px / 10 -> round(4.7) = 5
  ruler.dispatch("pointerdown", { offsetX: 47, preventDefault() {} });
  assertEq(FR.current(), 5, "pointerdown at 47px snaps to frame 5");
  assertEq(pxToNum(ph.style.left), 5 * pxPerFrame, "playhead follows scrub to 50px");

  // drag to 83px -> round(8.3) = 8
  ruler.dispatch("pointermove", { offsetX: 83 });
  assertEq(FR.current(), 8, "pointermove (scrubbing) at 83px snaps to frame 8");
  console.log(`  scrubber: 47px->frame ${5}, 83px->frame ${FR.current()}`);
}

// ── Test 4: step(+1)/step(-1) move by exactly 1 and clamp at bounds ──
{
  const container = createElement("div");
  const durationSec = 1;
  const fps = 10;            // totalFrames = 10, bounds [0, 9]
  const pxPerFrame = 6;
  FR.build(container, { durationSec, fps, pxPerFrame, currentFrame: 5 });
  const ph = findPlayhead(container);

  assertEq(FR.current(), 5, "starts at frame 5");
  assertEq(FR.step(1), 6, "step(+1): 5 -> 6");
  assertEq(FR.step(1), 7, "step(+1): 6 -> 7");
  assertEq(FR.step(-1), 6, "step(-1): 7 -> 6");
  assertEq(pxToNum(ph.style.left), 6 * pxPerFrame, "playhead left tracks stepped frame");

  // clamp at upper bound (totalFrames-1 == 9)
  FR.seekToFrame(9);
  assertEq(FR.step(1), 9, "step(+1) clamps at totalFrames-1 (9)");
  assertEq(pxToNum(ph.style.left), 9 * pxPerFrame, "playhead clamps at upper bound px");

  // clamp at lower bound (0)
  FR.seekToFrame(0);
  assertEq(FR.step(-1), 0, "step(-1) clamps at 0");
  assertEq(pxToNum(ph.style.left), 0, "playhead clamps at 0px");

  // prev/next BUTTON clicks drive the same step logic
  const prevBtn = container.querySelector(".frame-step-btn"); // first = prev
  const nextBtn = container.querySelectorAll(".frame-step-btn")[1];
  FR.seekToFrame(4);
  nextBtn.dispatch("click", {});
  assertEq(FR.current(), 5, "next button: 4 -> 5");
  prevBtn.dispatch("click", {});
  assertEq(FR.current(), 4, "prev button: 5 -> 4");
  console.log(`  step: bounds [0,${fps - 1}], clamp-up=9, clamp-down=0, buttons OK`);
}

// ── Test 5: build() attaches a complete ruler subtree to its container ─
// This is the exact same buildFrameRuler() that renderProjectTimeline() wires
// into the #project-timeline-ruler host next to #project-timeline-tracks, so a
// passing build() here proves the panel-render path produces a real subtree.
{
  const host = createElement("div");
  FR.build(host, { durationSec: 5, fps: 24, pxPerFrame: 4, currentFrame: 0 });
  assert(findRuler(host) != null, "build attaches a .frame-ruler to the given container");
  assert(findPlayhead(host) != null, "build attaches a .playhead");
  assert(host.querySelector(".frame-step") != null, "build attaches a .frame-step control group");
  assertEq(host.querySelectorAll(".frame-step-btn").length, 2, "frame-step has prev + next buttons");
  assertEq(tickCount(host), Math.round(5 * 24), "panel-style build: ticks == round(5*24)=120");

  // Rebuilding into the same host (as the panel does on every render) replaces,
  // not duplicates: clear innerHTML then rebuild, exactly like the panel code.
  host.innerHTML = "";
  FR.build(host, { durationSec: 1, fps: 30, pxPerFrame: 6, currentFrame: 3 });
  assertEq(tickCount(host), 30, "rebuild into same host: ticks reflect new duration (30), no leftovers");
  assertEq(FR.current(), 3, "rebuild preserves requested currentFrame=3");
}

console.log(`PASS (${checks} checks)`);
process.exit(0);
