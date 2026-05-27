# Lumeri v3-alive — context for option A planning

This file is input for the next planning pass (Opus → A spec).
Not user-facing. Snapshot of branch `claude/jolly-clarke-JO7E3` as of
commit `39c4e30` (2026-05-27, after F1 fix).

## 1. Code volume on the branch

Branch is 11 commits ahead of `main`, +3596 insertions, 0 deletions
across 22 files. Everything additive: v2 (`gemia/agent_workflow.py`
4573 lines, plus opencode_compat / runtime_vnext / orchestrator /
creative_sandbox / stability / lumerai.sandbox, ~8793 lines total) is
untouched and still loadable.

### v3 production code

| File | Lines | Role |
|---|---:|---|
| `gemia/agent_loop_v3.py` | 587 | drive_turn loop, render_messages, session meta writer |
| `gemia/gemini_client.py` | 217 | real SSE streaming OpenRouter client |
| `gemia/budget_guard.py` | 123 | only host-side gate (cost + time) |
| `gemia/transport/sse.py` | 95 | per-session queue + iter_events |
| `gemia/prompts/system_v3.md` | 86 | creative-collaborator framing |
| `gemia/transport/__init__.py` | 0 | package marker |
| `server.py` (additive) | +14 | `GET /sessions/{id}/stream` route |

### Tools layer

| File | Lines | Role |
|---|---:|---|
| `gemia/tools/_schema.py` | 313 | **all 15** verb schemas (10 are model-visible stubs) |
| `gemia/tools/_context.py` | 171 | AssetRegistry / ToolContext / ProgressUpdate / contains() |
| `gemia/tools/_ffmpeg.py` | 169 | run_ffmpeg_with_progress + ffprobe helpers |
| `gemia/tools/__init__.py` | 80 | DISPATCHER registry (5 real + 10 NotImplementedError stubs) |
| `gemia/tools/edit_video.py` | 184 | trim/concat/reverse/speed via Popen + out_time_us |
| `gemia/tools/add_overlay.py` | 150 | text/image/subtitle via drawtext + overlay |
| `gemia/tools/color_grade.py` | 119 | 6 named looks + free-form + intensity blend |
| `gemia/tools/export.py` | 115 | 5 quality × 4 format × 6 platform combinations |
| `gemia/tools/analyze_media.py` | 112 | ffprobe + 512×512 thumbnail (video/image/audio) |

**Subtotals**: agent core 1022, tools 1413, scripts+evidence+docs 1090.
**Total new lines on branch**: ~3596 insertions.

## 2. v3-alive milestone result (2026-05-27)

Prompt: `把 /tmp/clip.mp4 前 5 秒裁掉,加暖色调,导出 1080p`
Input: 10s testsrc 1280x720@30fps + 440Hz sine (302KB mp4)

Model behaviour (no host guidance):
- t+5.04s — `edit_video(asset_id=v_001, operation=trim, trim={start_sec:5, end_sec:null})`
- t+9.13s — `color_grade(asset_id=v_002, look="warm", intensity=1)`
- t+15.86s — `export(asset_id=v_003, format="mp4", quality="1080p")`
- t+23.35s — streams a Chinese summary (4 text_delta chunks)
- t+23.82s — `turn_complete(final_asset_ids=[v_002, v_003, v_004])`

Output `v_004.mp4`: **1920×1080 h264 yuv420p 5.000s** at 382.6 kb/s.
25 SSE events captured, 23.82s total elapsed.

Full event log at `docs/v3-alive-evidence.log`.

## 3. Acceptance criteria (all PASS)

| # | Check | Result |
|---|---|---|
| a | `grep -rn "thinking\|executing\|reviewing\|思考中\|执行中\|审查中\|分析中\|处理中" gemia/agent_loop_v3.py gemia/tools/` | 0 matches |
| b | v2 `SessionStore` reads v3 `meta.json` | 10 fields parsed, turns/ dir exists, no crash |
| c | `docs/v3-alive-evidence.log` | 503 lines, 25 `data:` events with real timestamps |
| d | `ffprobe v_004.mp4` | 1920×1080 h264, 5.000000s |

## 4. Codex review (2026-05-27) — 3 P2 bugs

Codex `exec review --base main` flagged 3 real bugs none of which
triggered in the milestone path:

- **F1** [FIXED in 39c4e30]: `max_tool_steps` not enforced inside a
  single assistant message's batched `tool_calls`. Outer while-loop
  cap only fires at top of next iteration. Fix: in-loop cap check
  before `tool_steps_this_turn += 1`, emit `budget_gate` +
  `needs_approval` and `continue` (not break — silent drop would
  mislead the model). Verified by `scripts/smoke_step_cap_v3.py`
  (FakeGeminiClient returns 10 tool_calls; asserts 8 dispatches +
  2 `budget_gate` + 10 tool messages + 1 `turn_error`).

- **F2** [parked in `docs/v3-todo.md`]: `edit_video` speed path
  hardcodes `[0:a]` + `-map [a]`, crashes on silent video inputs.
  ETA ~10 lines + 1 smoke test.

- **F3** [parked in `docs/v3-todo.md`]: `add_overlay` image kind
  reuses the drawtext `_POSITIONS` table; overlay filter has a
  different variable namespace (`W/H/w/h` vs `text_w/text_h`), so
  centered/right/bottom image overlays fail. ETA ~12 lines + 1
  smoke test.

## 5. Current shippable capability boundary

### Can do today (proven by milestone or smoke tests)

1. Run a single in-process agent session end-to-end.
2. Real streaming function calling against Gemini 3.1 Pro on
   OpenRouter (SSE, fragmented tool-call args reassembled).
3. Dispatch any combination of 5 verbs:
   - `edit_video` trim/concat/reverse/speed (silent-video speed
     broken, see F2)
   - `color_grade` 6 named looks + free-form + intensity blend
   - `add_overlay` text/image/subtitle, 9 named positions (image
     positions broken for non-top-left, see F3)
   - `analyze_media` ffprobe + 512×512 thumbnail with Plan-B
     visual feedback path
   - `export` mp4/mov/webm/gif × 4k/1080p/720p/480p/draft ×
     6 platform tweaks
4. Per-session budget guard returns `needs_approval`; model
   decides what to do (no host auto-substitute).
5. Per-turn cap (`max_tool_steps=8`) now enforced both at the
   outer loop and inside batched tool_calls.
6. Independent visual-inspection cap
   (`max_visual_inspections=3`) only fires on `analyze_media`,
   never shares storage with the tool cap.
7. Emit SSE event log to `gemia.transport.sse.REGISTRY`, served
   via `GET /sessions/{id}/stream` (server.py route in place).
8. Write v2-`SessionStore`-compatible `meta.json` (and empty
   `turns/` dir) so legacy loaders can read v3 sessions.

### Cannot do today (option B / option A scope)

- 10 of 15 verbs are stubs: `generate_image` / `generate_video`
  (Veo) / `generate_audio` (Lyria), `edit_image`, `composite`,
  `arrange_timeline`, `mix_audio`, `transform_geometry`,
  `extract_frame`, `search_library`. Schemas are exposed to the
  model; calls raise `NotImplementedError`.
- No HTTP endpoint to create a session (`POST /sessions` missing).
- No HTTP endpoint to send a turn (`POST /sessions/{id}/turn`
  missing).
- No HTTP endpoint to upload source assets (frontend can't
  provide a clip).
- No way to authenticate `/sessions/{id}/stream` subscribers
  (anyone can listen).
- No persistence of conversation history, asset registry, or
  events between process restarts. `meta.json` only carries
  `turn_count` + identity.
- No frontend SSE consumer. Tauri app at 7788 still talks to v2
  opencode_compat routes; never opens an EventSource against
  `/sessions/{id}/stream`.
- Concurrent multi-session correctness is untested (data
  structures appear safe; no stress test).
- File-path `preview_uri` in `tool_exec_result` is the on-disk
  path, not a browser-fetchable URL. Frontend will need a
  separate `GET /sessions/{id}/assets/{asset_id}` endpoint or
  similar — open design question (see prerequisite 3 below).

## 6. Next phase: option A — Tauri SSE integration for real creative tasks

Selected by user 2026-05-27. Acrab + Claude will discuss four
prerequisite decisions in chat before Opus produces an A spec:

1. **HTTP endpoint protocol schema** —
   `POST /sessions` request/response shape,
   `POST /sessions/{id}/turn` (and whether turns can be
   submitted with attachments inline or only by asset_id
   reference), `POST /sessions/{id}/assets` upload contract
   (multipart? signed body? size cap?), how `session_id` is
   generated (server-allocated UUID vs client-proposed slug),
   how the streaming endpoint is gated (session-token query
   param? cookie? bearer header?).

2. **Frontend turn state machine** —
   how `model_text_delta` → progressively rendered assistant
   bubble; how `model_tool_call_start/ready/exec_*` chain
   collapses into a single "step row" with status pill; how
   `budget_gate` is surfaced (modal? inline?); how `turn_error`
   ends an interrupted turn; how the Plan-B thumbnail user
   message is displayed; what happens to the composer between
   turn_start and turn_complete (disabled? cancelable?).

3. **Asset preview_uri visibility** —
   today the event carries an on-disk path like
   `/private/tmp/lumeri-v3-milestone/.../v_002.mp4`. Frontend
   can't fetch that directly. Options: (a) introduce
   `GET /sessions/{id}/assets/{asset_id}` route, (b) write
   outputs under a static dir already served by server.py
   (`/file/...`), (c) data-URL inline (only for small assets),
   (d) signed time-limited URLs. Affects where dispatchers
   write outputs and how the AssetRegistry exposes them.

4. **SSE reconnect strategy** —
   `gemia.transport.sse.iter_events` blocks on the queue and
   closes on a sentinel. There is no replay buffer: if the
   browser drops connection mid-turn it cannot catch up.
   Options: (a) keep events for last N seconds with
   `Last-Event-ID` replay, (b) terminate the turn on
   disconnect, (c) persist events to disk and stream from
   offset on reconnect. Affects how the SSE registry stores
   events and the recovery semantics on the frontend.

Once these four are settled, Opus produces an A spec and Claude
implements per spec in milestone-sized chunks (same workflow as
the v3-alive sequence).

## 7. Reset points

- Rollback to v2: `git checkout main` — branch is purely additive,
  no v2 file modified except `server.py` (+14 lines for SSE route,
  preserves all existing routes; revert with a one-line patch).
- All v3 in-memory state lives in one AgentLoopV3 instance per
  session; restarting the host process discards every active
  session.
- v3 commits on branch: 11 total, named `v3:` prefix; F1 fix is
  the most recent (`39c4e30`).
