#!/usr/bin/env node
// Frontend integration test for the v3 project-timeline panel: proves the
// round-1 frame ruler and round-2 keyframe-track editor are actually WIRED into
// renderProjectTimeline(), not just buildable in isolation.
//
// Strategy (no browser, mirrors v3_frame_ruler_test.mjs / v3_keyframe_editor_
// test.mjs): load the REAL static/v3/v3.js inside a node `vm` context backed by
// a minimal DOM shim, then drive the public hook window.__lumeriTimelineUI and
// assert COMPUTED values:
//
//   - renderPanel(fixture): a .frame-ruler lands inside #project-timeline-ruler
//     with tick count == round(durationSec * fps)
//   - seekToFrame(k): the panel playhead left px == k * pxPerFrame
//   - step(+1)/step(-1): the header timecode readout's frame index moves by
//     exactly 1 (and the SMPTE MM:SS:FF rolls correctly)
//   - selectClip(id): the keyframe strip renders that clip's markers at
//     left px == (clip.start + keyframe.t) * pxPerSec
//
// Run: node tests/v3_timeline_frame_ui_test.mjs  (prints PASS / exits 0 on success)

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

// ── minimal DOM shim (same shape as the round-1/round-2 frontend tests) ─
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

// Mirror index.html: the project-timeline panel needs the meta element to live
// INSIDE a header so updatePlayheadReadout() can append the timecode span to a
// real parent, exactly like the shipped markup.
const BOOT_IDS = [
  "session-id-label", "connection-pill", "new-session-btn", "timeline",
  "empty-state", "asset-grid", "upload-input", "upload-btn", "prompt-input",
  "send-btn", "sandbox-toggle-btn",
];
for (const id of BOOT_IDS) {
  const node = createElement("div");
  node.id = id;
  body.appendChild(node);
}

// project-timeline panel with the same nesting as index.html
const panel = createElement("section");
panel.id = "project-timeline-panel";
body.appendChild(panel);
const header = createElement("div");
header.className = "project-timeline-header";
panel.appendChild(header);
const metaEl = createElement("span");
metaEl.id = "project-timeline-meta";
header.appendChild(metaEl);
const tracksEl = createElement("div");
tracksEl.id = "project-timeline-tracks";
panel.appendChild(tracksEl);
for (const id of ["pt-edit-hint", "pt-split-btn", "pt-delete-btn", "pt-undo-btn"]) {
  const node = createElement("button");
  node.id = id;
  panel.appendChild(node);
}

// ── globals: fetch / EventSource / storage / timers / navigator ──────
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

const UI = windowObj.__lumeriTimelineUI;
assert(UI && typeof UI.renderPanel === "function", "window.__lumeriTimelineUI.renderPanel is exposed");
assert(typeof UI.seekToFrame === "function", "window.__lumeriTimelineUI.seekToFrame is exposed");
assert(typeof UI.step === "function", "window.__lumeriTimelineUI.step is exposed");
assert(typeof UI.selectClip === "function", "window.__lumeriTimelineUI.selectClip is exposed");
assert(typeof UI.readout === "function", "window.__lumeriTimelineUI.readout is exposed");

// The panel render path uses the same default px-per-frame as the standalone
// ruler hook; the keyframe strip uses fps * pxPerFrame px-per-second.
const PX_PER_FRAME = 6;   // FRAME_RULER_DEFAULT_PX_PER_FRAME in v3.js

// ── helpers ──────────────────────────────────────────────────────────
function pxToNum(s) { return Number(String(s).replace("px", "")); }
function panelEl() { return document.getElementById("project-timeline-panel"); }
function rulerHost() { return document.getElementById("project-timeline-ruler"); }
function kfHost() { return document.getElementById("project-timeline-keyframes"); }
function rulerTickCount() { return rulerHost().querySelectorAll(".frame-tick").length; }
function panelPlayhead() { return rulerHost().querySelector(".playhead"); }
function readoutFrame() {
  // "MM:SS:FF · f<frame>" -> the integer after "f"
  const txt = UI.readout() || "";
  const m = txt.match(/f(\d+)/);
  return m ? Number(m[1]) : NaN;
}
function readoutTimecode() {
  const txt = UI.readout() || "";
  const m = txt.match(/^(\d\d:\d\d:\d\d)/);
  return m ? m[1] : "";
}

// ── fixture project (same shape as GET /sessions/{id}/timeline) ──────
// A clip whose keyframes are explicit so derived marker positions are
// deterministic: clip.effects.keyframes.opacity maps frameIndex -> value.
const FPS = 30;
const DURATION = 4;                       // -> totalFrames = round(4*30) = 120
const PX_PER_SEC = FPS * PX_PER_FRAME;    // 180
const CLIP_START = 1.0;                   // seconds; keyframe t is offset by this
const fixture = {
  session_id: "S1",
  project_id: "P1",
  patch_seq: 7,
  duration: DURATION,
  fps: FPS,
  width: 1920,
  height: 1080,
  tracks: [
    {
      id: "v1", kind: "video", name: "v1",
      clips: [
        {
          id: "clipA", name: "intro", media_kind: "video", track_id: "v1",
          start: CLIP_START, duration: 2.0, source_in: 0, source_out: 2.0,
          // frame indices 0, 15, 60 at fps=30 -> t = 0s, 0.5s, 2.0s
          effects: { keyframes: { opacity: { "0": 0.0, "15": 1.0, "60": 0.0 } } },
        },
      ],
    },
  ],
};

// ── Test 1: panel render wires the frame ruler with the right tick count ─
{
  UI.renderPanel(fixture);
  assertEq(panelEl().hidden, false, "panel is shown after renderPanel");
  assert(rulerHost() != null, "renderPanel creates #project-timeline-ruler host");
  assert(rulerHost().querySelector(".frame-ruler") != null, "a .frame-ruler is wired into the ruler host");

  const totalFrames = Math.round(DURATION * FPS); // 120
  assertEq(UI.currentFrame(), 0, "playhead starts at frame 0");
  assertEq(rulerTickCount(), totalFrames, "ruler tick count == round(durationSec*fps)");

  // ruler width == totalFrames * pxPerFrame
  const ruler = rulerHost().querySelector(".frame-ruler");
  assertEq(pxToNum(ruler.style.width), totalFrames * PX_PER_FRAME, "ruler width px == totalFrames*pxPerFrame");
  console.log(`  panel-ruler: dur=${DURATION}s fps=${FPS} -> ticks=${rulerTickCount()} (expect ${totalFrames})`);
}

// ── Test 2: seeking the panel ruler moves the playhead to frame*pxPerFrame ─
{
  const ph = panelPlayhead();
  for (const k of [0, 1, 30, 77, 119]) {
    const ret = UI.seekToFrame(k);
    assertEq(ret, k, `panel seekToFrame(${k}) returns ${k}`);
    assertEq(pxToNum(ph.style.left), k * PX_PER_FRAME, `panel playhead left px == ${k}*${PX_PER_FRAME}`);
  }
  // clamp beyond bounds (totalFrames-1 == 119)
  assertEq(UI.seekToFrame(99999), 119, "panel seek beyond end clamps to totalFrames-1");
  assertEq(pxToNum(ph.style.left), 119 * PX_PER_FRAME, "panel playhead clamps at end px");
  console.log(`  panel-playhead: seek(77) -> left=${77 * PX_PER_FRAME}px, clamp-up=119`);
}

// ── Test 3: frame-step moves the header timecode readout by exactly 1 frame ─
{
  UI.seekToFrame(40);
  assertEq(readoutFrame(), 40, "readout shows frame 40 after seek");
  assertEq(readoutTimecode(), "00:01:10", "SMPTE at frame 40 fps=30 is 00:01:10");

  const before = readoutFrame();
  UI.step(1);
  assertEq(readoutFrame(), before + 1, "step(+1) advances readout frame by exactly 1");
  assertEq(readoutTimecode(), "00:01:11", "SMPTE advances to 00:01:11");

  UI.step(-1);
  assertEq(readoutFrame(), before, "step(-1) moves readout frame back by exactly 1");
  assertEq(readoutTimecode(), "00:01:10", "SMPTE back to 00:01:10");

  // frame 29 -> +1 rolls the seconds field (fps=30: 00:00:29 -> 00:01:00)
  UI.seekToFrame(29);
  assertEq(readoutTimecode(), "00:00:29", "SMPTE at frame 29 is 00:00:29");
  UI.step(1);
  assertEq(readoutFrame(), 30, "step(+1) across the second boundary -> frame 30");
  assertEq(readoutTimecode(), "00:01:00", "SMPTE rolls to 00:01:00 at frame 30");
  console.log(`  panel-step: 40->41->40 ok, second-roll 29->30 = ${readoutTimecode()}`);
}

// ── Test 4: selecting a clip renders its keyframe markers at t*pxPerSec ─
{
  const markers = UI.selectClip("clipA");
  assert(Array.isArray(markers), "selectClip returns marker positions");
  assertEq(markers.length, 3, "clipA has 3 derived keyframe markers (frames 0,15,60)");

  // derived t from frame indices, offset by the clip start
  const expectedT = [0, 15, 60].map((fr) => CLIP_START + fr / FPS); // [1.0, 1.5, 3.0]
  markers.forEach((m, i) => {
    assertClose(m.t, expectedT[i], `marker[${i}] t == clip.start + frame/fps (${expectedT[i]})`);
    assertClose(m.left, expectedT[i] * PX_PER_SEC, `marker[${i}] left px == t*pxPerSec (${expectedT[i] * PX_PER_SEC})`);
  });

  // the markers are actually in the DOM under the panel's keyframe host
  const domMarkers = kfHost().querySelectorAll(".kf-marker");
  assertEq(domMarkers.length, 3, "3 .kf-marker elements rendered in the panel keyframe host");
  const domLefts = domMarkers.map((m) => pxToNum(m.style.left));
  domLefts.forEach((left, i) => {
    assertClose(left, expectedT[i] * PX_PER_SEC, `DOM marker[${i}] left px == t*pxPerSec`);
  });

  // the property name flows through to the track
  const track = kfHost().querySelector(".kf-track");
  assertEq(track.getAttribute("data-property"), "opacity", "keyframe track carries the derived property name");
  console.log(`  panel-keyframes: clipA opacity markers t=[${expectedT.join(",")}] -> left=[${domLefts.join(",")}]px`);
}

// ── Test 5: deriving keyframes from a clip with NO animation data ─────
// A bare clip (no effects/keyframes) yields a single "clip" track with
// boundary markers at clip-relative t=0 and t=duration.
{
  const bare = { id: "bare", start: 0.5, duration: 1.5, media_kind: "image" };
  const tracks = UI.clipKeyframeTracks(bare, FPS);
  assertEq(tracks.length, 1, "bare clip yields exactly one fallback track");
  assertEq(tracks[0].property, "clip", "fallback track property is 'clip'");
  assertEq(JSON.stringify(tracks[0].keyframes.map((k) => k.t)), JSON.stringify([0, 1.5]),
    "fallback boundary markers at t=0 and t=duration");

  // and the derived data still renders into the panel via selection
  const fixture2 = JSON.parse(JSON.stringify(fixture));
  fixture2.tracks[0].clips.push(bare);
  UI.renderPanel(fixture2);
  const m = UI.selectClip("bare");
  assertEq(m.length, 2, "selecting a bare clip renders 2 boundary markers");
  // offset by the bare clip's start (0.5s)
  assertClose(m[0].left, (0.5 + 0) * PX_PER_SEC, "bare marker[0] left px == start*pxPerSec");
  assertClose(m[1].left, (0.5 + 1.5) * PX_PER_SEC, "bare marker[1] left px == (start+dur)*pxPerSec");
  console.log(`  fallback: bare clip -> boundary markers at left=[${m.map((x) => x.left).join(",")}]px`);
}

console.log(`PASS (${checks} checks)`);
process.exit(0);
