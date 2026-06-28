#!/usr/bin/env node
// Frontend test for the v3 RUNNING ACTIVITY LOG (Claude-Code-style narration).
//
// Strategy (mirrors v3_inspector_test.mjs / v3_frame_ruler_test.mjs): load the
// REAL static/v3/v3.js inside a node `vm` context backed by a minimal DOM shim,
// then drive the public debug/test hook window.__lumeriNarration with a FIXTURE
// SSE event sequence and assert the COMPUTED rendered output — not just that a
// function exists.
//
// Fixture sequence asserted here:
//   turn_start
//   model_text_delta("Generating the hero image")
//   model_tool_call_start(generate_image, {prompt:"…"})
//   model_tool_call_ready (args land)
//   tool_exec_start
//   tool_exec_progress (sub-status)
//   tool_exec_result({asset_id:"img1"})
//   ... then a SECOND tool that fails: tool_exec_error
//   turn_complete
//
// Asserts the rendered timeline HTML/state contains:
//   - a progress/activity line bearing the tool name 'generate_image'
//   - the narration text ("Generating the hero image")
//   - a done/✓ marker + the result asset id for the successful tool
//   - an error marker (✗) + the error message for the failed tool
//   - existing handlers (turn_complete) still flip turnInProgress off
//
// Run: node tests/v3_narration_test.mjs   (prints PASS / exits 0 on success)

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
function assertIncludes(haystack, needle, msg) {
  assert(typeof haystack === "string" && haystack.includes(needle),
    `${msg} (substring ${JSON.stringify(needle)} not found in computed output)`);
}
function assertExcludes(haystack, needle, msg) {
  assert(typeof haystack === "string" && !haystack.includes(needle),
    `${msg} (substring ${JSON.stringify(needle)} unexpectedly present)`);
}

// ── minimal DOM shim (same shape as v3_inspector_test.mjs) ───────────
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

// ── globals (no real network/TTY/keys) ───────────────────────────────
function fetchStub(url, init) {
  // Boot's createSession() POSTs /sessions; resolve so state.sessionId is set
  // (so the activity line's asset preview link path renders). Everything else
  // (timeline polling, settings/sandbox) resolves benign or rejects quietly.
  if (typeof url === "string" && /\/sessions\/?$/.test(url) && init && init.method === "POST") {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ session_id: "test-session" }) });
  }
  if (typeof url === "string" && url.includes("/timeline")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ tracks: [], duration: 0 }) });
  }
  if (typeof url === "string" && url.includes("/settings/sandbox")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ sandbox_disabled: false }) });
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

const N = windowObj.__lumeriNarration;
assert(N && typeof N.dispatch === "function", "window.__lumeriNarration.dispatch is exposed");
assert(typeof N.startTurn === "function", "window.__lumeriNarration.startTurn is exposed");
assert(typeof N.render === "function", "window.__lumeriNarration.render is exposed");
assert(typeof N.buildArgPreview === "function", "window.__lumeriNarration.buildArgPreview is exposed");

// Let boot's async createSession() settle so state.sessionId is set.
for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));

// ── Test 1: buildArgPreview — short, human-readable, secret-safe ──────
{
  const p = N.buildArgPreview({ prompt: "a neon cyberpunk city at night", size: "1024x1024" });
  assertIncludes(p, "prompt:", "arg preview names the prompt key");
  assertIncludes(p, "size:", "arg preview names the size key");
  assertIncludes(p, "neon", "arg preview shows the prompt value text");

  // long value gets truncated with an ellipsis
  const longV = N.buildArgPreview({ prompt: "x".repeat(500) });
  assert(longV.length <= 90, `long arg preview is truncated (len=${longV.length})`);
  assertIncludes(longV, "…", "long value preview ends with ellipsis");

  // secret-ish keys are hidden, not previewed verbatim
  const secret = N.buildArgPreview({ api_key: "sk-SUPERSECRET-zzz", prompt: "hi" });
  assertExcludes(secret, "SUPERSECRET", "secret-ish value is NOT shown verbatim");
  assertIncludes(secret, "<hidden>", "secret-ish value is masked as <hidden>");
  console.log(`  argPreview: ${p}`);
  console.log(`  secret-safe: ${secret}`);
}

// ── Test 2: drive the full fixture event sequence ────────────────────
const turn = N.startTurn("make a hero image and a broken thing");

N.dispatch({ kind: "turn_start" });
assertEq(N.state.turnInProgress, true, "turn_start sets turnInProgress");

// narration streams in
N.dispatch({ kind: "model_text_delta", delta: "Generating the hero image" });
assertEq(turn.assistantText, "Generating the hero image", "model_text_delta accumulates narration");
assertEq(turn.streaming, true, "narration marks the turn streaming");

// the successful tool: generate_image
N.dispatch({ kind: "model_tool_call_start", call_id: "c1", tool_name: "generate_image" });
// the streamed narration becomes the call's lead-in reasoning (existing behavior)
const tc1 = turn.toolCalls.get("c1");
assert(tc1, "tool call c1 registered");
assertEq(tc1.reasoning, "Generating the hero image", "pre-call narration captured as reasoning");
N.dispatch({ kind: "model_tool_call_ready", call_id: "c1", args: { prompt: "a neon cyberpunk city" } });
N.dispatch({ kind: "tool_exec_start", call_id: "c1" });
assertEq(tc1.status, "running", "tool_exec_start -> running");
N.dispatch({ kind: "tool_exec_progress", call_id: "c1", percent: 40, message: "rendering" });
assertEq(tc1.progress.message, "rendering", "tool_exec_progress sub-status recorded");

// render WHILE running and assert the live activity line shows running glyph + tool name + arg preview
N.render();
{
  const html = N.timelineHTML();
  assertIncludes(html, "activity-line running", "running tool renders a running activity line");
  assertIncludes(html, "⏺", "running activity line shows the ⏺ glyph");
  assertIncludes(html, "generate_image", "activity line bears the tool name generate_image");
  assertIncludes(html, "prompt:", "activity line shows the arg preview key");
  assertIncludes(html, "rendering", "running sub-status appears under the line");
}

// result lands
N.dispatch({ kind: "tool_exec_result", call_id: "c1", result: { asset_id: "img1", summary: "hero image ready" } });
assertEq(tc1.status, "done", "tool_exec_result -> done");
assertEq(tc1.previewAssetId, "img1", "result asset id recorded");

// the failing tool
N.dispatch({ kind: "model_tool_call_start", call_id: "c2", tool_name: "run_shell" });
N.dispatch({ kind: "model_tool_call_ready", call_id: "c2", args: { cmd: "ffmpeg -i missing.mp4 out.mp4" } });
N.dispatch({ kind: "tool_exec_start", call_id: "c2" });
N.dispatch({ kind: "tool_exec_error", call_id: "c2", error: "ffmpeg: no such file" });
const tc2 = turn.toolCalls.get("c2");
assertEq(tc2.status, "failed", "tool_exec_error -> failed");

// turn completes (existing handler must still flip the flag)
N.dispatch({ kind: "turn_complete", deliverable_asset_ids: ["img1"] });
assertEq(N.state.turnInProgress, false, "turn_complete clears turnInProgress (no regression)");
assertEq(turn.complete, true, "turn_complete marks the turn complete (no regression)");

// ── Test 3: final rendered timeline contains the whole readable log ──
N.render();
const html = N.timelineHTML();

// tool name(s)
assertIncludes(html, "generate_image", "final render: progress line bears tool name generate_image");
assertIncludes(html, "run_shell", "final render: progress line bears tool name run_shell");

// narration text (captured as the call's reasoning lead-in)
assertIncludes(html, "Generating the hero image", "final render: narration text is shown");

// done / ✓ marker + result for the successful tool
assertIncludes(html, "activity-line done", "final render: successful tool has a done activity line");
assertIncludes(html, "✓", "final render: done marker ✓ present");
assertIncludes(html, "hero image ready", "final render: one-line result summary shown");
assertIncludes(html, "img1", "final render: result asset id shown");

// error marker + message for the failed tool, distinct style
assertIncludes(html, "activity-line failed", "final render: failed tool has a failed activity line");
assertIncludes(html, "✗", "final render: error marker ✗ present");
assertIncludes(html, "al-error", "final render: error uses the distinct al-error style");
assertIncludes(html, "ffmpeg: no such file", "final render: error message shown");

// secret-safety end to end: no api_key would have leaked (none here, but verify
// the masking path is wired through the real render, not just the helper)
{
  const t2 = N.startTurn("secret test");
  N.dispatch({ kind: "model_tool_call_start", call_id: "s1", tool_name: "call_api" });
  N.dispatch({ kind: "model_tool_call_ready", call_id: "s1", args: { token: "sk-LEAKME-123", q: "ping" } });
  N.dispatch({ kind: "tool_exec_start", call_id: "s1" });
  N.render();
  const h2 = N.timelineHTML();
  assertExcludes(h2, "sk-LEAKME-123", "render path never leaks a secret-ish arg value");
  assertIncludes(h2, "&lt;hidden&gt;", "render path masks secret-ish arg as <hidden> (escaped)");
}

console.log(`  fixture log rendered: generate_image ✓ (img1) · run_shell ✗ (ffmpeg error) · narration shown`);
console.log(`PASS (${checks} checks)`);
