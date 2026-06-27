#!/usr/bin/env node
// Frontend test for the v3 visual-polish pass.
//
// The polish pass is mostly CSS, but it ships ONE computed behavior: a full
// SMPTE timecode formatter HH:MM:SS:FF exposed as window.__lumeriTimecode.
// This test loads the REAL static/v3/v3.js inside a node `vm` context backed by
// the same minimal DOM shim the other v3 node tests use, then asserts:
//
//   - window.__lumeriTimecode is a function
//   - timecode formatting is correct for several (frame, fps) cases, including
//     the spec anchors: (90, 30) -> "00:00:03:00" and (47, 24) -> "00:00:01:23"
//   - the formatter is integer-only / deterministic (rounds + clamps inputs)
//   - the output shape is always HH:MM:SS:FF with hours present
//   - `node --check static/v3/v3.js` passes (no syntax regressions)
//
// It does NOT touch CSS (no browser) and does NOT modify any existing test.
//
// Run: node tests/v3_polish_test.mjs   (prints PASS / exits 0 on success)

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const V3_JS = path.resolve(__dirname, "../static/v3/v3.js");
const V3_CSS = path.resolve(__dirname, "../static/v3/v3.css");

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

// ── minimal DOM shim (same shape as the other v3 node tests) ─────────
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

// Pre-create the elements v3.js looks up at boot (mirrors index.html ids).
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

// ── Test 1: the polish timecode hook exists and is a function ────────
const TC = windowObj.__lumeriTimecode;
assert(typeof TC === "function", "window.__lumeriTimecode is exposed as a function");

// ── Test 2: spec anchor cases (HH:MM:SS:FF) ──────────────────────────
assertEq(TC(90, 30), "00:00:03:00", "frame 90 @ 30fps -> 00:00:03:00");
assertEq(TC(47, 24), "00:00:01:23", "frame 47 @ 24fps -> 00:00:01:23");
console.log(`  timecode: f90@30 -> ${TC(90, 30)}, f47@24 -> ${TC(47, 24)}`);

// ── Test 3: more computed cases across all four fields ───────────────
assertEq(TC(0, 30), "00:00:00:00", "frame 0 -> all zeros");
assertEq(TC(29, 30), "00:00:00:29", "frame 29 @ 30fps -> last frame of second 0");
assertEq(TC(30, 30), "00:00:01:00", "frame 30 @ 30fps rolls to second 1");
assertEq(TC(1800, 30), "00:01:00:00", "frame 1800 @ 30fps -> exactly 1 minute");
assertEq(TC(108000, 30), "01:00:00:00", "frame 108000 @ 30fps -> exactly 1 hour");
assertEq(TC(108090, 30), "01:00:03:00", "frame 108090 @ 30fps -> 1h 0m 3s 0f");
assertEq(TC(23, 24), "00:00:00:23", "frame 23 @ 24fps -> last frame of second 0");
assertEq(TC(24, 24), "00:00:01:00", "frame 24 @ 24fps rolls to second 1");
console.log(`  fields: 1min -> ${TC(1800, 30)}, 1hr -> ${TC(108000, 30)}, 1h3s -> ${TC(108090, 30)}`);

// ── Test 4: output shape is always HH:MM:SS:FF (four 2-digit fields) ──
for (const [fr, fps] of [[0, 30], [90, 30], [47, 24], [108090, 30], [5000, 25]]) {
  const out = TC(fr, fps);
  assert(/^\d{2}:\d{2}:\d{2}:\d{2}$/.test(out), `TC(${fr},${fps})="${out}" matches HH:MM:SS:FF shape`);
}

// ── Test 5: integer-only / deterministic (rounds + clamps inputs) ────
assertEq(TC(89.6, 30), "00:00:03:00", "fractional frame 89.6 rounds to 90");
assertEq(TC(-5, 30), "00:00:00:00", "negative frame clamps to 0");
assertEq(TC(90, 0), TC(90, 30), "fps 0 falls back to 30 (same as default)");
assertEq(TC(90, 29.6), "00:00:03:00", "fps 29.6 rounds to 30");
// Idempotent: same inputs always yield the same string.
assertEq(TC(47, 24), TC(47, 24), "formatter is deterministic for identical inputs");
console.log(`  robust: 89.6 -> ${TC(89.6, 30)}, -5 -> ${TC(-5, 30)}, fps0 -> ${TC(90, 0)}`);

// ── Test 6: header readout format is UNCHANGED (regression guard) ────
// The existing v3_timeline_frame_ui_test parses the panel header readout with
// /^(\d\d:\d\d:\d\d)/ expecting MM:SS:FF. Confirm the source still produces that
// (MM:SS:FF · f<frame>) form and did NOT switch the header to HH:MM:SS:FF, so
// the polish hook is purely additive and the existing test stays green.
assert(/\$\{fmtTimecode\(fr\.currentFrame, fr\.fps\)\} · f\$\{fr\.currentFrame\}/.test(src),
  "panel header still uses fmtTimecode (MM:SS:FF) — header format unchanged");
assert(src.includes("window.__lumeriTimecode"),
  "v3.js still exports window.__lumeriTimecode");

// ── Test 7: CSS polish present (additive refined palette) ────────────
// Light proof the polish pass actually touched the stylesheet with the new
// NLE palette vars, without depending on a browser layout engine.
const css = fs.readFileSync(V3_CSS, "utf8");
for (const v of ["--track-head", "--clip-video", "--radius-sm"]) {
  assert(css.includes(v), `v3.css defines refined palette var ${v}`);
}

// ── Test 8: node --check static/v3/v3.js passes (no syntax regression) ─
let checkOk = true;
try {
  execFileSync(process.execPath, ["--check", V3_JS], { stdio: "pipe" });
} catch (e) {
  checkOk = false;
  console.error(`node --check failed: ${e.stderr ? e.stderr.toString() : e.message}`);
}
assert(checkOk, "node --check static/v3/v3.js passes");
console.log("  node --check static/v3/v3.js: OK");

console.log(`PASS (${checks} checks)`);
process.exit(0);
