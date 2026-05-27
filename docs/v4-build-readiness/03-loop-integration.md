# 03 — Adding `execute_skill` to `agent_loop_v3`

> Date: 2026-05-28
> Scope: how a "model writes a Python script that runs in a sandbox" verb plugs into the loop that already exists.

## Naming: `execute_skill` vs `compose` vs `run_script`

The framing matters because it sets the model's mental model.

| Name | What it tells the model |
|---|---|
| `execute_skill` | "you are picking one of N pre-existing things to run". Carries the v2 baggage. |
| `compose` | "you are stitching primitives into a new composition". Editorial, not authorship. |
| `run_script` | "you are writing new code that runs". Closest to honest. |
| `build` | "you are constructing a custom workflow this user needs that no canned verb covers". Honest and motivates the use case. |

**Recommendation: `build`.** It says exactly what the verb is — a fallback for the times the 15 canned verbs aren't enough. The verb description in the schema can be explicit: *"Write a short Python script that uses the lumeri.* primitives. Call this when no other verb covers what the user asks for."* That framing tells Gemini both when to use it and what not to use it for.

`execute_skill` is misleading — there is no skill catalog the model is picking from. `compose` is editorial-sounding but doesn't tell the model "you can write code". Reserve `compose` for a future "stitch existing assets together" verb if needed.

## How it plugs in

`agent_loop_v3.AgentLoopV3._drive_turn` (line ~470 in current file) calls `DISPATCHER[tc.name](parsed_args, self._tool_ctx)` for every tool the model picks. The DISPATCHER is just a dict mapping tool name → async dispatcher (`gemia/tools/__init__.py:55-70`).

**Adding `build` is a one-line registration plus a new dispatcher module.** No changes to drive_turn, accumulator, SSE transport, or budget guard logic.

```
gemia/tools/build.py            # new ~150-200 lines: dispatcher + sandbox driver
gemia/tools/_schema.py          # +1 schema entry for "build"
gemia/tools/__init__.py         # +1 line: "build": _build.dispatch
```

The verb appears alongside `analyze_media` / `edit_video` / etc. The loop doesn't know it's special.

### Schema sketch (not a design, just to verify shape)

```json
{
  "name": "build",
  "description": "Write and run a short Python script when none of the other verbs cover what's needed. The script runs in a sandbox and can import the `lumeri` package (which re-exports the primitive library) to call image, audio, and video operations. Use this for novel compositions; prefer the dedicated verbs (edit_video, color_grade, etc.) when they fit.",
  "parameters": {
    "type": "object",
    "required": ["script", "produces_asset_kind"],
    "properties": {
      "script": {
        "type": "string",
        "description": "Python source. Must define an `OUTPUT_PATH` global pointing to the final asset path (relative to workdir). Stdin asset paths are exposed via the `INPUTS` dict."
      },
      "produces_asset_kind": {
        "type": "string",
        "enum": ["video", "image", "audio"]
      },
      "inputs": {
        "type": "object",
        "description": "Mapping from script-side variable name → asset_id."
      },
      "summary": {
        "type": "string",
        "description": "One-sentence description of what the script does, for the user-facing log."
      }
    }
  }
}
```

## Relationship to the existing 15 verbs

**Fallback, not replacement.** The 15 verbs are still the right tool when they fit — they're cheaper (no script-writing tokens, no sandbox overhead), they have better args schemas the model can fill confidently, and they have human-readable summaries baked in (which the model uses in its user reply, per the M3 observation).

`build` is what the model reaches for when:
- The user wants something compound that doesn't decompose cleanly into existing verbs.
- The user wants something parametrically unique ("make 10 versions with the color shift varying linearly from blue to red").
- The user wants something the 15-verb vocabulary doesn't address at all ("render a particle effect at this position from t=2s to t=5s").

The system prompt should make this hierarchy explicit. Otherwise the model may default to `build` for things `edit_video` already does (it's strictly more general).

## tool_result shape: how sandbox output rides the wire

The dispatcher returns a dict. The existing event flow (`agent_loop_v3.py:540-580`) handles arbitrary keys, with two reserved special keys (`thumbnail_path`, `thumbnail_for_next_message`) that get stripped before sending to the model. That pattern extends cleanly:

```python
# return from build dispatcher
return {
    "asset_id": "v_007",                  # produced asset, registered
    "summary": "ran 47-line script: 10-frame parallax sweep",
    "metadata": {
        "script_sha256": "...",
        "ran_in_sec": 4.2,
        "exit_code": 0,
        "produced_files": [...],          # paths discovered post-run
    },
    "stdout_tail": "...last 2KB...",      # model-visible
    "stderr_tail": "...last 2KB...",      # model-visible, shown only if exit != 0
    # NO thumbnail_for_next_message — the model can choose to follow up with analyze_media
}
```

The model sees `summary` + `stdout_tail` + `stderr_tail` as the tool_result content. The agent loop's `_scrub_local_paths` helper (already exists, `agent_loop_v3.py:127`) handles the path-leakage problem for free.

**The SSE event emitted to the frontend** can additionally carry `script` (the full source the model wrote) so the UI renders an expandable code block. This is honest reporting — the user sees exactly what ran, not a synthesized description.

## Error loop: traceback → revise → re-run

This is the load-bearing question for whether v4 build is useful.

The agent_loop_v3 turn structure today supports this directly. Each model→tool round-trip is its own iteration of the `while True:` in drive_turn. If `build` raises (sandbox returns exit != 0), the dispatcher catches it as currently done for any tool (line 517-540) and surfaces:

```json
{
  "kind": "tool_exec_error",
  "tool_name": "build",
  "error": "exit code 1: ModuleNotFoundError: No module named 'lumeri.color'\n  File \"<script>\", line 5, ...",
  ...
}
```

The model sees this in the next message (as a `tool` role result with the error text), can read the traceback, write a corrected script, call `build` again. **No new loop machinery needed** — this is exactly the model→tool→model cycle the loop already does for the 5 implemented verbs.

The one tuning knob: `max_tool_steps` (default 8) bounds how many build attempts the model gets per user turn. Default 8 is probably right (gives the model 3-5 retries plus a few other tool calls). If real use shows model gets stuck in script-fix loops, the cap fires and the user sees a `turn_error` with the partial work — which is the honest behavior.

**One subtlety:** the model might write a script that imports a primitive that doesn't exist in `lumeri`. We could:
- (a) Let the import fail at runtime and surface the traceback (model corrects on next turn). Costs 1 round-trip + sandbox spawn for each typo.
- (b) Pre-flight the script: AST-parse it, check imports against the catalog, return a static-check error before sandbox spawn. Saves the spawn but adds a tool-side validation step.

Pre-flight is the right answer for tight loops. ~30 lines of extra code.

## Visual feedback: how `build` output gets seen

`build` produces an asset_id. The model can call `analyze_media(asset_id=...)` on it as usual to get a thumbnail (and Plan-B visual feedback via the next user message). The existing analyze_media path covers this without change.

If we want `build` to also auto-show a thumbnail (analogous to how analyze_media does it), we can have the dispatcher return `thumbnail_for_next_message=True` like analyze_media — but I'd argue against this default. The model should ask for the thumbnail explicitly if it wants to verify; otherwise we're back to the "host decides when the model needs to see something" anti-pattern v3 was designed to avoid.

## Token cost estimate

Per `build` cycle, rough envelope:

| Stage | Tokens |
|---|---:|
| Model writes initial script (50-150 lines) | 500-1500 output |
| Tool result with stdout/stderr_tail (success) | 200-800 input |
| Tool result with traceback (failure) | 500-2000 input |
| Model writes revised script (only diff in practice) | 200-800 output |
| Model's final user reply | 100-300 output |

**One successful build (1 attempt):** ~1000-2500 tokens. **One build with one fix (2 attempts):** ~3000-5500 tokens. **Pathological 5-attempt loop:** ~10000-15000 tokens, capped by `max_tool_steps`.

vs. a single canned-verb call: ~50-150 tokens of args + ~200 of summary. So `build` is **15-50× more expensive per use** than calling a canned verb. That's the right pressure — the model should reach for canned verbs first.

The Plan-B visual feedback (`analyze_media`) costs roughly the same regardless of how the asset was produced.

## What the work actually is, in hours

| Task | Hours |
|---|---|
| Carve the `lumeri/` package — `from gemia.picture import *` style, with `__all__` curation against the 813-primitive registry | 4-6 |
| Write `gemia/tools/build.py` dispatcher: sandbox-exec wrapper, script writeout, OUTPUT_PATH/INPUTS protocol, stdout/stderr capture, post-run asset discovery + registration | 8-12 |
| Pre-flight AST check: parse model's script, validate imports against `lumeri.*` catalog, fail fast with a useful error | 2-3 |
| Schema entry + dispatcher registration + `__init__` wiring | 1 |
| System prompt update: when to use `build` vs canned verbs, what the `lumeri` package exposes | 2-3 |
| Smoke test: model writes 3 increasingly hard scripts, all run end-to-end | 3-4 |
| Integration test: end-to-end via the v3-A HTTP surface | 2 |
| **Total before any UX polish** | **~22-31 hours (3-4 days)** |

Excluded from this estimate: the work to curate which primitives `lumeri.*` exposes (architectural), and the work to fix the 70 ndarray-taking primitives (already audited in doc 01). Those are separate efforts.

## What changes vs what stays the same in agent_loop_v3

| Component | Change? |
|---|---|
| `drive_turn` main loop | None |
| `_StreamAccumulator` | None |
| `_parse_args` | None |
| `_scrub_local_paths` | None — already handles arbitrary dict scrubbing |
| `_make_progress_cb` | None — sandbox stdout can pipe to it for line-by-line progress if we want |
| `_append_tool_result` | None |
| Budget guard | Maybe add a per-build cost estimate (~$0.02 from token estimate × OpenRouter Gemini pricing) so the guard fires when the model's gone into a fix-loop |
| SSE event kinds | Add `model_script_writing` (optional, for UX) — or just let the model's text deltas show the script being written. |
| `system_v3.md` | +1 paragraph on `build` |

The HTTP layer (`v3_routes.py`), session manager, frontend (`static/v3/`) — **zero changes required**. The frontend renders `tool_exec_result` cards uniformly; `build` cards just have a longer summary and an expandable `script` blob.

---

*Verified against current `gemia/agent_loop_v3.py` (629 lines after recent linter edits), `gemia/tools/__init__.py` (DISPATCHER structure), and `gemia/tools/analyze_media.py` (existing in-band-special-return pattern).*
