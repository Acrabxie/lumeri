# Lumeri Runtime Kernel Golden Workflow

This document is the stable developer entry for the experimental Lumeri
Runtime Kernel. It is intentionally small: prove the coding-kernel loop before
connecting it to the default 7788 product path.

## Boundary

- This path is experimental.
- It must stay behind `LUMERAI_SCRIPT_MODE=1`.
- It must not silently affect Plan-v2, the skill router, stability gate, or the
  default 7788 UI.
- The host is the only timeline writer. Scripts emit `TimelinePatch` JSON; they
  do not mutate project files directly.
- A script that emits no `TimelinePatch` is not a success.
- Full sandbox stderr belongs in task JSON/logs, not in user-facing command
  output.

## Current API Surface

The script-facing `lumerai` package exposes only:

- `timeline_state()`
- `clip_load()`
- `clip_trim()`
- `clip_color_grade()`
- `timeline_insert()`
- `timeline_replace()`

Do not expand this API until the current loop is boringly reliable.

## Project Layout

Persistent projects live under the selected root:

```text
projects/<project_id>/
  state.json
  seed.json
  meta.json
  patches/0001.json
  patches_discarded/
  renders/0001-preview.json
```

`state.json` is the current snapshot. `patches/` is append-only for active
history. Undo moves discarded patch files aside; it does not delete history.

## One-Command Smoke

Use the helper script when you want a clean command-level check:

```bash
python3 scripts/lumerai_runtime_kernel_smoke.py \
  --video /absolute/path/to/input.mp4
```

The script creates a canonical seed project, runs the golden trim+warm-grade
script, inspects the project, renders a low-res preview, undoes to seq 0,
reruns the script, renders again, and inspects again. It prints one JSON
summary.

By default, smoke artifacts are written under:

```text
/Volumes/Extreme SSD/GemiaTemp/lumeri-runtime-kernel-smoke/
```

If the external disk is unavailable, it falls back to `temp/` inside the repo.

## Manual Golden Workflow

When debugging by hand, run the same flow explicitly.

```bash
export LUMERAI_SCRIPT_MODE=1
export PYTHONPATH="/Volumes/Extreme SSD/gemia:${PYTHONPATH:-}"

python3 -m gemia lumerai-script \
  --script tests/scripts/lumerai_trim_grade_insert.py \
  --project-id smoke_kernel \
  --project-init-from /path/to/seed.json \
  --session-id smoke_1 \
  --root /path/to/smoke-root

python3 -m gemia lumerai-inspect \
  --project-id smoke_kernel \
  --history 1 \
  --root /path/to/smoke-root

python3 -m gemia lumerai-render \
  --project-id smoke_kernel \
  --label first \
  --root /path/to/smoke-root

python3 -m gemia lumerai-undo \
  --project-id smoke_kernel \
  --to-seq 0 \
  --root /path/to/smoke-root

python3 -m gemia lumerai-script \
  --script tests/scripts/lumerai_trim_grade_insert.py \
  --project-id smoke_kernel \
  --session-id smoke_2 \
  --root /path/to/smoke-root

python3 -m gemia lumerai-render \
  --project-id smoke_kernel \
  --label rerun \
  --root /path/to/smoke-root

python3 -m gemia lumerai-inspect \
  --project-id smoke_kernel \
  --format text \
  --history 1 \
  --root /path/to/smoke-root
```

Expected result:

- first script run succeeds with `patch_seq_end == 1`
- inspect reports 2 clips
- render writes a playable H.264 MP4 under `outputs/runtime/<project_id>/`
  and a manifest under `projects/<project_id>/renders/`
- undo reports `from_seq == 1`, `to_seq == 0`
- rerun succeeds with `patch_seq_start == 1`
- rerun render has an ffprobe-readable manifest with `source_clips`
- final inspect reports 2 clips and an undo log tail
- no command prints a raw Python traceback

## Hidden Desktop vNext

The old 7788 UI remains the default. The rebuilt shell is hidden behind:

```bash
LUMERAI_VNEXT=1 python3 -m gemia server --host 127.0.0.1 --port 7788
```

Then open:

```text
http://127.0.0.1:7788/next
```

The gated runtime API is:

- `POST /runtime/session`
- `POST /runtime/message`
- `GET /runtime/events/<session_id>`
- `GET /runtime/project/<project_id>`
- `POST /runtime/approval`
- `POST /runtime/feedback`

These routes use the Runtime Kernel only. They do not call Plan-v2 or the old
skill router.

## Canned Agent Smoke

`lumerai-agent` can run without a live provider:

```bash
python3 -m gemia lumerai-agent \
  --project-id smoke_kernel \
  --session-id smoke_agent \
  --goal "insert a short trim" \
  --max-turns 3 \
  --canned-script /path/to/agent_script.py \
  --root /path/to/smoke-root
```

Expected result:

- `experimental == true`
- `status == "done_marker"` if the canned script contains `# DONE`
- `final_clip_count` increases only when a real patch was emitted

## Acceptance Standard

For this kernel, "done" means:

- command-level smoke passes
- focused tests pass
- project patch history is inspectable
- preview render manifests are ffprobe-readable
- undo preserves audit history
- failure is structured and honest
- no default UI path changed
