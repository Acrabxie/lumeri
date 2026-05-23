# Lumeri vNext Recovery - 2026-05-23

## What Antigravity Actually Left On Disk

The shared queue claimed Antigravity Opus 4.6 implemented async `/runtime/message`, `/runtime/task/<task_id>`, frontend polling, and tests.

Current checkout verification showed only one real code change from that claim:

- `server.py` had a shallow `/runtime/task/<task_id>` alias to the legacy `/task/<task_id>` handler.

The vNext files named in the queue were not present in the current tree:

- `static/next.html`
- `gemia/runtime_vnext.py`
- `tests/test_server_static_web.py`

`http://localhost:7788/next` returned `404` before recovery.

## Codex Recovery

Codex restored the vNext source from the final 2026-05-17 backup:

- `/Volumes/Extreme SSD/GemiaBackups/versions/20260517-114836-1af84d1-dirty-vnext-hide-internal-runtime-notices-followup-execute-final/gemia-source.tar.gz`

Before restoring, Codex saved the pre-restore state here:

- `/Volumes/Extreme SSD/GemiaBackups/versions/20260523-155037-codex-pre-vnext-restore`

Codex then implemented the real async handoff:

- `POST /runtime/message` defaults to HTTP `202` with `{status:"accepted", session_id, project_id, task_id}`.
- `POST /runtime/message?sync=1` preserves the old blocking response for tests and compatibility.
- `GET /runtime/task/<task_id>` returns the background task status and final result.
- The worker appends terminal `succeeded`/`failed` events so `/next` can show progress and final state.
- `static/next.html` now polls `/runtime/events/<session_id>` and `/runtime/task/<task_id>` after accepted submissions.

## Verification

- `python3 -m py_compile server.py gemia/runtime_vnext.py tests/test_server_static_web.py tests/test_lumerai_runtime_kernel.py`
- `python3 -m pytest tests/test_server_static_web.py -q` -> 24 passed
- `python3 -m pytest tests/test_lumerai_runtime_kernel.py -k 'runtime_vnext or generate_script' -q` -> 18 passed
- `python3 -m pytest tests/test_codex_self_loop.py tests/test_automation_loop.py -q` -> 22 passed
- Live API smoke: `/runtime/message` returned `202 accepted`, and `/runtime/task/<task_id>` completed with `succeeded`.
- Browser smoke: `http://localhost:7788/next` loaded, accepted a lightweight `hi` command, showed `接收了任务`, replied naturally, and ended with `完成了。` with no console warnings/errors.

