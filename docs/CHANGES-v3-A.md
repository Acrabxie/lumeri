# Lumeri v3-A branch — what changed and how to use it

> Branch: `claude/jolly-clarke-JO7E3`
> Target audience: Acrab a month from now, or anyone picking this up
> from a cold start.

---

## 简介

This branch turns Lumeri's "v3" agent loop — built in a separate
phase as `claude/jolly-clarke-JO7E3` *up through v3-alive* — into
something you can actually drive from a browser, end to end. The v2
flow (`gemia/agent_workflow.py`, 4573 lines) was a one-shot Plan-JSON
generator followed by Python pretending to narrate live agent
activity with hardcoded Chinese status strings. The model spoke once
and never again. The "progress" the UI showed was theatre.

v3 (already on this branch before A) replaced that with a real
function-calling agent loop: Gemini 3.1 Pro on OpenRouter, SSE
streaming, 5 ffmpeg verb dispatchers, real ffprobe progress, no
host-synthesized status text. A.M1 through A.M3 (this batch) adds
the surface around it — HTTP endpoints (create session, submit turn,
upload asset, fetch asset with Range, SSE stream with reconnect),
per-session worker threads, and a vanilla HTML/JS frontend at
`/v3`. It's the smallest amount of code that gets the v3 loop into a
user's hands.

What you can do today: upload a video, type a Chinese prompt, watch
the model pick verbs and execute them, see real ffmpeg progress,
play the resulting MP4 in-browser. What you cannot do: use the 10
not-yet-implemented verbs (generate_image / generate_video / etc.),
resume a session after a process restart, or rely on automatic
session cleanup. The v2 Tauri UI at `/` is untouched and still
works; v3 lives at `/v3` as a parallel surface.

---

## 架构变更

### 旧 (v2, still present, untouched)

```
User prompt
   │
   ▼
gemia/agent_workflow.run_agent_workflow() (4573 lines)
   │
   ├─ ONE call to Gemini → JSON plan
   │
   ├─ Python executes plan (no model in the loop)
   │
   └─ Python emits hardcoded Chinese status strings as if narrating
      ("我先确认这次要处理什么素材...")
```

### 新 (v3-alive + A)

```
Browser /v3 (vanilla HTML+JS, native EventSource)
   │
   ▼ POST /sessions, /turn, /assets       ▲ SSE: id:N\ndata:{...}\n\n
server.py _Handler (ThreadingHTTPServer)─┘
   │
   ▼ try_handle(self, method=…)  (one dispatch call per verb)
gemia/v3_routes.py
   │
   ▼
gemia/session_manager.SessionManager
   │   dict[session_id → SessionRunner]
   ▼
SessionRunner  (one daemon thread + one asyncio loop per session)
   │
   ▼ run_coroutine_threadsafe(agent.run_turn(msg))
gemia/agent_loop_v3.AgentLoopV3.drive_turn()
   │
   ├─► gemia/gemini_client.GeminiClientV3.stream_turn(messages, tools)
   │      real SSE from OpenRouter, parses chunked function-calling
   │      deltas: text_delta, tool_call_start, tool_call_args_delta,
   │      finish.
   │
   ├─► gemia/tools/DISPATCHER[tool_name](args, ctx)
   │      ctx is a per-session ToolContext with AssetRegistry +
   │      output_dir + progress callback.
   │      Real handlers: analyze_media, edit_video, color_grade,
   │      add_overlay, export. Stubs (10): NotImplementedError.
   │      Each real handler invokes ffmpeg via Popen + parses
   │      out_time_us from stdout for true progress; emits via the
   │      progress callback the loop wires in.
   │
   └─► self._emit({"kind": "...", ...})  for every visible event
              │
              ▼
gemia/transport/sse.REGISTRY.emit(session_id, event)
   monotonic per-session event_id, append to 200-entry ring buffer,
   put on a queue.
              │
              ▼ consumed by
iter_events(session_id, last_event_id=N) in /sessions/{id}/stream
   replays buffer (id > N), then drains queue.
              │
              ▼ SSE chunks back to Browser EventSource
              │
              ▼ browser tracks lastEventId; reconnect sends it as
                Last-Event-ID header automatically.
```

### What's different in one sentence

The model is in the loop on every step; progress is the FFmpeg's own
clock; the host narrates nothing.

---

## 代码组织

Read order if you're approaching the agent loop cold (top is most
important):

| File | One-line job | Read priority |
|---|---|---|
| `gemia/agent_loop_v3.py` | The loop. `drive_turn` is the heart — read it. | **core** |
| `gemia/tools/__init__.py` | Maps verb name → async dispatcher. 5 real, 10 stub. | **core** |
| `gemia/tools/_schema.py` | All 15 verb schemas (JSON sent to Gemini). Pure data. | **core** |
| `gemia/tools/_context.py` | `AssetRegistry` (per-session, asset_id → path), `ToolContext`, `ProgressUpdate`. | **core** |
| `gemia/transport/sse.py` | Per-session event queues + 200-entry ring buffer for reconnect. | **core** |
| `gemia/gemini_client.py` | Streams from OpenRouter with `stream:true`, reassembles fragmented tool-call args. | **core** |
| `gemia/budget_guard.py` | Per-session $ and time cap. Returns `needs_approval`; model decides. | **core** |
| `gemia/session_manager.py` | `SessionRunner` (thread + asyncio loop per session) and `SessionManager` (process-wide dict). | **core** |
| `gemia/v3_routes.py` | All HTTP endpoints. `try_handle` is the single entrypoint server.py calls. | **core** |
| `gemia/prompts/system_v3.md` | The system prompt. Creative-collaborator framing. Has `{{asset_registry}}` and `{{pinned_intent}}` placeholders. | **core** |
| `gemia/tools/_ffmpeg.py` | `run_ffmpeg_with_progress` (Popen + `-progress pipe:1` parsing), `ffprobe_metadata`. | edge |
| `gemia/tools/edit_video.py` | trim / concat / reverse / speed via the ffmpeg helper. | edge |
| `gemia/tools/color_grade.py` | Named looks (warm/cool/vintage/cinematic/teal_orange/neutral) + intensity blend. | edge |
| `gemia/tools/add_overlay.py` | text (drawtext) / image (overlay) / subtitle. Needs libfreetype-enabled ffmpeg. | edge |
| `gemia/tools/analyze_media.py` | ffprobe + 512×512 thumbnail. Returns `thumbnail_for_next_message=True` to trigger Plan-B visual feedback. | edge |
| `gemia/tools/export.py` | mp4/mov/webm/gif × 4k/1080p/720p/480p/draft × 6 platform presets. | edge |
| `static/v3/index.html` | Page skeleton. 1.5 KB. | edge |
| `static/v3/v3.js` | The frontend. 11 event handlers, no silent drop. ~400 lines. | edge |
| `static/v3/v3.css` | All styling. ~250 lines. | edge |
| `scripts/smoke_*.py` | Per-component smoke tests. Read when one breaks. | reference |
| `scripts/milestone_v3.py` | v3-alive end-to-end without HTTP. | reference |
| `scripts/integration_v3_m1.py` | A.M1 HTTP integration test (real server, real Gemini). | reference |
| `scripts/m3_real_task.py` | A.M3 real task driver (Playwright headless). | reference |

### Server-side modifications to v2 files

| File | What | Net lines |
|---|---|---|
| `server.py` | Two `try_handle` dispatchers in `_handle_get_like` + `do_POST`; static `/v3` route. Nothing else touched. | +24 |

### v2 assets we deliberately did NOT touch (still loadable)

| File | Lines | Why kept |
|---|---:|---|
| `gemia/agent_workflow.py` | 4573 | Rollback path. Still serves `/` UI via opencode_compat routes. |
| `gemia/orchestrator.py` | 674 | v2 dispatcher; called by agent_workflow. |
| `gemia/opencode_compat.py` | 509 | OpenCode-shape compat layer that the Tauri 7788 UI talks to. |
| `gemia/runtime_vnext.py` | 766 | Earlier vNext sandbox attempt. |
| `gemia/creative_sandbox*.py` | 1502 | Sandbox layer. |
| `gemia/stability.py` | 196 | stability_gate. |
| `lumerai/sandbox.py` | 276 | AST blacklist sandbox. |
| `gemia/agent_loop.py` | 297 | Earlier (non-v3) agent loop experiment. |
| Total kept untouched | **~8793** | All survive `git checkout main`. |

### v3 production code total

| Bucket | Lines |
|---|---:|
| Core agent loop | 1287 (agent_loop_v3 + gemini_client + budget_guard + system_v3.md) |
| Tools layer | 1413 (5 dispatchers + schema + context + ffmpeg helper) |
| HTTP/session/transport | 671 (v3_routes + session_manager + sse) |
| Frontend | 777 (index.html + v3.js + v3.css) |
| `server.py` net additions | 24 |
| Total v3 production | **~4172** |

Plus ~1500 lines of test/integration scripts and ~1000 lines of
evidence logs/docs.

---

## 怎么跑

### One-time setup

1. `~/.gemia/config.json` must contain at least
   `{"openrouter_api_key": "sk-or-v1-...", "proxy": "http://127.0.0.1:7890"}`
   (proxy optional but recommended in the user's location).
2. ffmpeg must be the homebrew-ffmpeg/ffmpeg tap build (8.1.1+), not
   the standard homebrew formula — the latter lacks `libfreetype`,
   which breaks `add_overlay`. Verify:
   `ffmpeg -filters 2>&1 | grep drawtext` should return a line.
3. Python deps: standard lib only for the agent loop. ffprobe/ffmpeg
   must be on PATH.

### Run

```sh
cd "/Volumes/Extreme SSD/gemia"
python3 server.py --port 7788 --host 127.0.0.1
```

Then open `http://127.0.0.1:7788/v3` in any modern browser (Chrome,
Safari, the Tauri app's webview).

A new session is created automatically on page load. The UI shows
the session_id, a `live` connection pill, an upload button, and a
prompt textarea. Drop a clip, type what you want, click `send`.

### 5-minute reproduction (Acrab cold start)

If you forget how it works:

```sh
cd "/Volumes/Extreme SSD/gemia"
git checkout claude/jolly-clarke-JO7E3
python3 scripts/m3_real_task.py
```

This spawns the server on a free port, generates a test clip,
drives the UI via headless Playwright, and writes
`docs/v3-A-M3-real-task.md` + screenshots. If that report shows
"turn completed = true" and the ffprobe of v_006 is 1920×1080, the
whole pipeline is healthy.

### Configuration knobs

| Env var | Default | Effect |
|---|---|---|
| `OPENROUTER_API_KEY` | (config file) | Overrides `~/.gemia/config.json` key. |
| `LUMERI_V3_MODEL` | `google/gemini-3.1-pro-preview` | Which OpenRouter model. |
| `OPENROUTER_PROXY` | (config file) | HTTP proxy. |
| `LUMERI_V3_OUTPUT_ROOT` | `/tmp/lumeri-v3` | Where session workdirs + meta live. |
| `LUMERI_V3_UPLOAD_MAX_BYTES` | `524288000` (500 MiB) | Upload size cap. |
| `LUMERI_PORT` / `GEMIA_PORT` | `7788` | Server port. |

---

## 已知问题

### Carried over from v3-alive backlog (`docs/v3-todo.md`)

- **F2** `edit_video` `speed` crashes on silent videos (`gemia/tools/edit_video.py:142-144`)
- **F3** `add_overlay` `image` kind reuses `_POSITIONS` text-w/text-h
  expressions; overlay filter needs `W/H/w/h`
  (`gemia/tools/add_overlay.py:107-111`)

### New, surfaced during A.M1–M3 implementation

- **A1** Sessions are never auto-collected. `SessionManager._runners` grows
  monotonically. For a single user this is fine for hours; for a
  long-running daemon it's a leak. Fix idea: idle-timeout sweeper
  thread.
- **A2** SSE replay buffer is 200 events per session. Long turns
  (many tool calls + lots of model text deltas) can exceed this. A
  reconnecting client that missed > 200 events will silently get
  only the most recent 200; there is no "you missed events" signal
  to the client. Fix idea: at reconnect, if `last_event_id <
  oldest_in_buffer`, emit a synthetic `replay_gap` event so the
  client can re-fetch from POST-restart context. (Don't auto-emit
  fake events without flagging — that violates the no-fake-event
  invariant.)
- **A3** `SessionRunner.close()` does `loop.stop()` then `thread.join(timeout=5)`.
  If the agent loop is mid-FFmpeg and FFmpeg takes > 5s, the thread
  outlives the join. Process exit reaps it (daemon thread) but a
  hot restart could leak file handles temporarily.
- **A4** EventSource auto-reconnect on browser network blips was
  tested only with `http.client` from Python (`integration_v3_m1.py`).
  The browser path *should* work because we emit `id:` lines and
  EventSource auto-sends `Last-Event-ID`, but it wasn't deliberately
  triggered with a real browser dropping connection mid-stream.
- **A5** The 10 stub verbs are visible to the model. If the model
  calls e.g. `generate_video`, the dispatcher raises
  `NotImplementedError` and the user sees a red `tool_exec_error`
  card. Not a bug but the UX is alarming. Either implement them
  (option B from earlier) or hide them from the schema (changes
  model behavior).
- **A6** Frontend's `final_asset_ids` semantics. The backend emits
  every newly-created asset in a turn as "final". The frontend
  *now* only marks the *last* one as the user-visible deliverable
  (fixed in M3). But this is a frontend convention; backend still
  reports the full list. Two-place truth.
- **A7** The `/v3` page auto-creates a session on every page load.
  If the user refreshes mid-task, they lose conversational state
  (the agent backend keeps the old session alive in memory until
  process exit, but the new page can't reach it). No "resume
  session" UX.
- **A8** No way to start a v3 session from the existing Tauri main
  UI at `/`. The user has to know to navigate to `/v3` manually.
  This is the #1 usability gap from Acrab's perspective.

(F4+ findings from Codex review of this batch will be appended to
`docs/v3-todo.md` as they come.)

---

## 回滚路径

### Switch back to v2 (zero v3 dependency)

```sh
git checkout main
python3 server.py
```

The `claude/jolly-clarke-JO7E3` branch is purely additive on the
codebase side. `server.py` net diff vs main: +24 lines, no removals
of existing routes. Every v2 file in the "kept untouched" table
above is byte-identical to main.

### Read a v3 session file using v2 code

Verified in `docs/v3-alive-evidence.log` and re-verified at the time
of writing this doc:

```sh
python3 -c "
from gemia.session_store import SessionStore
from pathlib import Path
s = SessionStore(Path('/tmp/lumeri-v3/sessions'))
import json
for sid_dir in s.root.iterdir():
    meta = json.loads(s.meta_path(sid_dir.name).read_text())
    print(meta)
"
```

v3 writes `meta.json` with all v2-`SessionStore`-expected fields
plus `loop_version: "v3"` (which v2 ignores).

### What's actually safe to delete later, when no longer rolling back

Once you trust v3 fully (months of real use), the legacy-deletion
candidates are documented in the original v3 spec:

- `gemia/agent_workflow.py` → move to `gemia/legacy/`
- `gemia/orchestrator.py` (slim to thin facade or remove)
- `gemia/opencode_compat.py` (kept while old UI at `/` is in use;
  remove after v3 fully replaces the main UI)
- `gemia/creative_sandbox*.py`, `lumerai/sandbox.py`,
  `gemia/runtime_vnext.py`, `gemia/stability.py`,
  `gemia/agent_loop.py`

Total deletable when you cut over: ~8793 lines.

---

## 下一步建议方向

You asked for my honest take, including whether I disagree with the
"M4 polish 不开,等用户反馈" decision. Here it is.

### Where I agree with you

M4 polish ("fix UI nits, refine status text, prettify") **is** a
trap right now. Acrab hasn't actually used the thing yet. Polish
needs feedback from real use, not me guessing.

### Where I disagree

There's a category of work that isn't "polish" but also isn't a new
milestone — it's **what stands between Acrab and actually using
v3**. Without it, you'll never get the feedback that justifies M4.
Specifically:

1. **A8 (no link from main UI to /v3) is the #1 thing.** Right now
   the v3 page is reachable only if you type `/v3` into the URL bar.
   Acrab will open the Tauri app, see the old UI, and either use
   that (and not exercise v3) or close the app frustrated. Adding a
   single button or tab in the existing nav that opens `/v3` is
   ~5 lines of edit in the Tauri main app — not a milestone, just
   a wiring fix. Do this before anything else.

2. **A4 (EventSource browser reconnect not actually verified).**
   Test it for real, with the browser, by killing the SSE
   connection mid-turn. 10 minutes of work. If it doesn't work,
   you find out now instead of when Acrab's Wi-Fi blips during a
   long export.

3. **F2 and F3 from the existing backlog.** They're both <15 lines
   each. Both will hit real usage soon (silent screen-recording
   videos and image overlays are common). Cheap to fix preemptively.

That's a 1-2 hour batch. Call it "A.M3.5", not "M4". Open it before
you tell Acrab to start using the thing.

### After Acrab starts using it

Then wait for feedback. The first real-use bugs will tell you what
M4 should be. My guess based on the code: it'll be about A1
(session leak), A5 (stub verb UX is alarming), or A7 (lose state on
refresh). Don't guess; let him tell you.

### When (eventually) doing milestone B (15-verb expansion)

When you do scale to 15 verbs, the most impactful additions are
`generate_image` (Nano Banana — cheap, fast, opens the "give me a
title card" use case) and `arrange_timeline` (multi-clip composition
— the unlock for actual editing workflows). `generate_video` (Veo)
is the most expensive, slowest, and least likely to be needed for
the festival-video case. Build the cheap visual ones first.

### When (eventually) cleaning legacy (option C)

Don't do this until v3 fully replaces the `/` Tauri UI. Otherwise
you break the rollback path that keeps you sleeping at night.

---

*Generated on 2026-05-27 after A.M3 passed. Branch state:
`claude/jolly-clarke-JO7E3` at commit `bdf9078`.*
