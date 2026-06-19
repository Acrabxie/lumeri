# GEMIA Change Log

## 2026-06-19

- Landed modern Lumeri into the mainline: the v3 orchestration rewrite, the v4 sandboxed build layer, and timeline v1 (M1–M8) — 39 model-callable verbs over a persistent, auditable project document.
- Added the v3 streaming agent loop where the model holds the wheel: fine-grained verbs, typed self-correcting tool errors, a per-tool circuit breaker, honest SSE progress (no fake progress), and `BudgetGuard` as the only host gate — no host keyword detection, no host verify.
- Added the v4 capability layer: kernel-isolated `sandbox-exec` code execution (`build`/`check_job`/`wait_for_job`/`save_skill`) with a shared async `JobRegistry`, host-side internet (`fetch`/`web_search`/`web_open`), sandboxed `run_shell`, and Vertex media generation (`generate_image`/`generate_video`/`generate_audio`) — credentials unreadable, network denied inside the sandbox, asset bytes never surfaced.
- Added timeline v1: a persistent multi-track timeline document with 14 patch ops where one verb = exactly one auditable, undoable TimelinePatch; `ripple` defaults off; low-res `render_preview`; and full-quality multi-track `project_export`.
- Added multi-track audio to export (M6): audio-track clips plus embedded video audio (kept unless the clip is muted), with `gain_db`/`fade_in`/`fade_out`/`muted` on the effects map.
- Added track-level ducking and a deterministic export length (M7): `timeline_set_track` + `duck_under` sidechain-compress a music bed under a voice trigger; export length is the audio-inclusive timeline master (video padded with black past the last clip).
- Added OpenTimelineIO interchange (M5/M8): bidirectional `project_export_otio`/`project_import_otio` with a `format` arg — `otio`/`otioz`/`otiod` (lossless, bundles carry media) and lossy `edl`/`fcp7`/`fcpx` via an optional `interop` extra, with a documented fidelity matrix and honest errors for missing adapters.

## 2026-05-24

- Added a `Copy path` action to the active Creative Runtime agent report card so non-media primary artifacts are actionable even when they cannot be opened as previews.
- Added a direct `Open preview` action to the active Creative Runtime agent report card when the reported primary path is previewable media.
- Added a source-of-truth sync/check path for the active Creative Runtime panel asset, so changes to the runtime report UI can be edited in `tauri-app/src/assets/creative-runtime-ui.js` and verified against the served dist bundle.
- Added a compact `agent_report` to normalized task payloads and surfaced it in the active Creative Runtime panel, so the original UI now shows run state, preview/artifact path, log/failure counts, and the next diagnostic step after a task.
- Added a UI-ready `brief` to Creative Dev Sandbox reports so run/shell responses can show a concise preview-ready or failure-revision summary without parsing the full diagnostic JSON.
- Added a Creative Dev Sandbox session report: `/runtime/dev/workspace/<session_id>/report`, sandbox run responses, and OpenCode shell responses now summarize recent files, commands, failures, latest preview, and the next diagnostic step so a Lumeri run is reviewable like a coding-agent session.

## 2026-05-23

- Surfaced completed preview/artifact filenames in the active Lumeri UI status stream, so successful runs now show which reviewable preview and artifacts were produced instead of only saying `完成`.
- Added first-class Creative Dev Sandbox preview reporting: `/runtime/dev/workspace/<session_id>/preview`, sandbox run responses, and OpenCode shell responses now return the latest preview artifact plus a raw URL, so agent runs can report exactly which reviewable preview was produced.
- Hardened Creative Dev Sandbox script execution so sandboxed Python commands can import `lumerai`, receive deterministic Runtime Kernel environment variables, fall back when `sandbox-exec` is present but unusable, and still produce a declared `previews/runtime-preview.mp4` through local HyperFrames/FFmpeg rendering.
- Extended `lm.hyperframes_render()` fallback behavior to handle an installed but failing HyperFrames CLI, not only a missing CLI, so preview generation can continue with the local FFmpeg path.
- Added a local FFmpeg fallback for `lm.hyperframes_render()` when the HyperFrames CLI is unavailable, while still writing the HyperFrames workspace project, manifest, and a reviewable MP4 so blank-canvas Runtime Kernel scripts can continue through preview generation.
- Added a `/next` `Preview` control for edited `runtime/script.py`: it saves dirty script changes, runs the script through the OpenCode-style shell path, declares `previews/runtime-preview.mp4` as the expected preview artifact, and loads the latest sandbox preview back into the preview pane.
- Added raw Creative Dev Sandbox file serving via `/runtime/dev/workspace/<session_id>/files?raw=1&kind=...&path=...`, with relative declared artifact paths resolved inside the sandbox workspace.
- Added a first-class `/next` `Run script.py` control that saves dirty `runtime/script.py` edits before executing `python3 scripts/runtime/script.py` through the Creative Dev Sandbox shell and OpenCode-style tool output path.
- Added a Creative Dev Sandbox read-file path for `/runtime/dev/workspace/<session_id>/files`, and wired `/next` to rehydrate saved `runtime/script.py` content without overwriting dirty editor changes or newer generated scripts.
- Added the first opencode-aligned vNext contract: `/session`, `/session/:id/message`, `/session/:id/prompt_async`, `/session/:id/shell`, `/event`, `/file`, and `/find` now map Lumeri Runtime Kernel and Creative Sandbox state into OpenCode-style sessions, message parts, tool parts, SSE events, and file reads.
- Restored the Lumeri Codex self-loop runner path expected by automation.
- Added deterministic self-loop artifacts: prompt, redacted context, manifest, and `codex-result.md` report under the repo workspace on the external SSD.
- Added focused tests for artifact creation and secret-like context redaction.
- Added `workspace-diagnostics.json` to each self-loop iteration so missing vNext/Runtime Kernel capability files become machine-readable recovery tasks with restore hints.
