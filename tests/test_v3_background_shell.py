"""Background run_shell lifecycle against the REAL dispatchers.

Drives run_shell / build with the sandbox disabled (raw bash) so the whole
process-group lifecycle is exercised end to end:

  * foreground hard-timeout SIGKILLs the WHOLE process group (a bare
    proc.kill() would orphan the `find`/subshell grandchild);
  * run_in_background returns immediately with a job_id instead of blocking;
  * check_job's since_offset returns only NEW bytes (the incremental-read fix);
  * kill_job kills a live job and is idempotent on an already-finished one;
  * the pending-shell cap is enforced and freed by killing a job;
  * kill_job refuses job kinds that have no local process (e.g. Veo LROs).

Every test cleans build._PROCESSES so a leaked child never bleeds into the
next test (autouse teardown killpg's whatever is still tracked).
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from gemia.sandbox_v4 import is_sandbox_disabled, set_sandbox_disabled
from gemia.tools import build, run_shell
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools._jobs import JobRegistry


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _disable_sandbox():
    """Run raw bash so we can spawn real sleepers/echoers and prove the killpg
    lifecycle; the sandboxed path is covered elsewhere. Restore afterwards."""
    was = is_sandbox_disabled()
    set_sandbox_disabled(True)
    yield
    set_sandbox_disabled(was)


@pytest.fixture(autouse=True)
def _clean_processes():
    """Reap any background child still tracked after a test, so a leaked sleeper
    can't survive into the next test or the suite tail."""
    yield
    for _job_id, (proc, _deadline) in list(build._PROCESSES.items()):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
    build._PROCESSES.clear()


def _ctx(tmp_path: Path, on_bg: Callable[[str], None] | None = None) -> ToolContext:
    extra: dict[str, Any] = {}
    if on_bg is not None:
        extra["on_background_job"] = on_bg
    return ToolContext(
        session_id="bg_shell_test",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        jobs=JobRegistry(),
        extra=extra,
    )


def test_foreground_timeout_kills_whole_process_group(tmp_path: Path) -> None:
    """A foreground command whose bash parent spawns a background grandchild:
    on timeout the WHOLE process group must be SIGKILLed, so the grandchild's
    delayed `touch` never runs. A bare proc.kill() on bash would leave the
    reparented subshell alive to create the sentinel."""
    ctx = _ctx(tmp_path)
    sentinel = tmp_path / "sentinel.txt"
    # Subshell touches the sentinel after 2s; bash itself blocks on sleep 30.
    cmd = f"( sleep 2 && touch '{sentinel}' ) & sleep 30"
    result = _run(run_shell.dispatch({"command": cmd, "timeout_sec": 1}, ctx))

    assert result["exit_code"] == 124  # coreutils timeout convention
    assert result["timed_out"] is True
    # Wait past the grandchild's 2s timer: if the group truly died, no sentinel.
    time.sleep(3)
    assert not sentinel.exists(), "process group not fully killed — orphan grandchild ran"


def test_background_submit_returns_immediately_nonblocking(tmp_path: Path) -> None:
    """run_in_background returns a job_id without waiting for the (30s) command,
    registers a kind='shell' job with pid/pgid, tracks it in the shared process
    table, and fires the on_background_job watcher hook exactly once."""
    fired: list[str] = []
    ctx = _ctx(tmp_path, on_bg=fired.append)

    start = time.monotonic()
    result = _run(
        run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"background submit blocked for {elapsed:.1f}s (should be instant)"
    assert result["status"] == "submitted"
    job_id = result["job_id"]
    assert job_id.startswith("shell_")
    assert result["timeout_sec"] == 600.0  # background default
    assert Path(result["log_path"]).parent.name == "tasks"
    assert result["summary"]

    record = ctx.jobs.get(job_id)
    assert record.kind == "shell"
    assert record.pid and record.pgid
    assert record.started_epoch is not None
    assert job_id in build._PROCESSES
    assert fired == [job_id]

    _run(build.dispatch_kill({"job_id": job_id}, ctx))


def test_check_job_since_offset_returns_only_new_bytes(tmp_path: Path) -> None:
    """After a short job finishes, check_job with since_offset==end returns no
    new bytes (truncated False), and since_offset==0 replays the full log —
    proving the incremental byte-offset read, not a full-file re-read."""
    ctx = _ctx(tmp_path)
    payload = "HELLO_BG_WORLD"
    result = _run(
        run_shell.dispatch(
            {"command": f"printf '{payload}'", "run_in_background": True}, ctx
        )
    )
    job_id = result["job_id"]

    waited = _run(build.dispatch_wait({"job_id": job_id, "max_wait_sec": 10}, ctx))
    assert waited["status"] == "done"
    assert waited["exit_code"] == 0
    end_offset = waited["next_offset"]
    assert end_offset == len(payload)

    # No offset → tail read of the (short) whole log.
    tail = _run(build.dispatch_check({"job_id": job_id}, ctx))
    assert payload in tail["stdout_tail"]

    # From the end → no new bytes, nothing truncated, offset unchanged.
    empty = _run(
        build.dispatch_check({"job_id": job_id, "since_offset": end_offset}, ctx)
    )
    assert empty["stdout_tail"] == ""
    assert empty["truncated"] is False
    assert empty["next_offset"] == end_offset

    # From zero → the full payload again, offset advances to the end.
    from_zero = _run(
        build.dispatch_check({"job_id": job_id, "since_offset": 0}, ctx)
    )
    assert from_zero["stdout_tail"] == payload
    assert from_zero["next_offset"] == end_offset
    assert from_zero["truncated"] is False


def test_kill_job_kills_live_process_and_is_idempotent(tmp_path: Path) -> None:
    """kill_job SIGKILLs a live background job (→ failed, killed=True, dropped
    from the process table) and is a no-op on an already-terminal job."""
    ctx = _ctx(tmp_path)
    result = _run(
        run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
    )
    job_id = result["job_id"]
    assert job_id in build._PROCESSES

    killed = _run(build.dispatch_kill({"job_id": job_id}, ctx))
    assert killed["status"] == "failed"
    assert killed["killed"] is True
    assert killed["error"] == "killed by kill_job"
    assert job_id not in build._PROCESSES
    assert ctx.jobs.get(job_id).last_polled_status == "failed"

    again = _run(build.dispatch_kill({"job_id": job_id}, ctx))
    assert again["already_finished"] is True
    assert again["killed"] is False
    assert again["status"] == "failed"


def test_pending_shell_job_cap_is_enforced_and_freed(tmp_path: Path) -> None:
    """At most _MAX_PENDING_SHELL_JOBS background shell jobs may be pending; the
    next submit raises, and killing one frees a slot for a fresh submit."""
    ctx = _ctx(tmp_path)
    cap = run_shell._MAX_PENDING_SHELL_JOBS
    ids = []
    for _ in range(cap):
        r = _run(
            run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
        )
        ids.append(r["job_id"])
    assert len(ids) == cap

    with pytest.raises(ValueError, match="too many pending background shell jobs"):
        _run(
            run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
        )

    # Free one slot → a new submit succeeds.
    _run(build.dispatch_kill({"job_id": ids[0]}, ctx))
    r = _run(
        run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
    )
    assert r["status"] == "submitted"
    # Remaining sleepers are reaped by the autouse _clean_processes fixture.


def test_pending_cap_counts_live_processes_not_stale_registry_status(tmp_path: Path) -> None:
    """Quick background jobs that have already EXITED must not count toward the
    pending cap even before the 2s watcher polls them: the cap counts live OS
    processes, not lagging registry status. (The old code counted
    list_pending() by status, so three instant `printf` jobs still marked
    'submitted' wrongly blocked a fourth submit.)"""
    ctx = _ctx(tmp_path)
    cap = run_shell._MAX_PENDING_SHELL_JOBS
    ids = []
    for _ in range(cap):
        r = _run(
            run_shell.dispatch({"command": "printf hi", "run_in_background": True}, ctx)
        )
        ids.append(r["job_id"])

    # Wait until every process has actually exited. Nothing has polled the
    # registry yet, so its status still lags at the pre-terminal 'submitted'.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(build._PROCESSES[j][0].poll() is not None for j in ids):
            break
        time.sleep(0.02)
    assert all(build._PROCESSES[j][0].poll() is not None for j in ids), "jobs never exited"
    assert all(
        ctx.jobs.get(j).last_polled_status not in ("done", "failed") for j in ids
    ), "registry should still lag at 'submitted' (nothing polled it)"

    # Cap counts live processes → all three are dead → a fresh submit is allowed.
    r = _run(
        run_shell.dispatch({"command": "sleep 30", "run_in_background": True}, ctx)
    )
    assert r["status"] == "submitted"
    _run(build.dispatch_kill({"job_id": r["job_id"]}, ctx))


def test_kill_job_rejects_job_kinds_without_a_local_process(tmp_path: Path) -> None:
    """kill_job only handles build/shell jobs; a remote LRO (e.g. a Veo video
    job) has no local process group and must be refused with a clear error."""
    ctx = _ctx(tmp_path)
    ctx.jobs.submit(
        kind="video",
        provider="ai_studio:veo-3.1-fast",
        operation_name="operations/deadbeef",
        pending_asset_id="v_001",
        estimated_eta_sec=120.0,
        summary="a veo job",
        job_id="video_deadbeef",
    )
    with pytest.raises(ValueError, match="only supports build/shell"):
        _run(build.dispatch_kill({"job_id": "video_deadbeef"}, ctx))
