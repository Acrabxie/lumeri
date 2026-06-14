# GEMIA Change Log

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
