"""run_shell — execute a bash command in an isolated sandbox.

The workspace directory is fully writable. Outside the workspace, files can
only be created, not modified/deleted. Credentials (~/.ssh, ~/.config/gcloud,
~/.gemia/config.json) are not readable. Network access is denied.

Wraps the command with sandbox-exec and build_v4_sandbox_command() from the
M1 isolation layer (gemia/sandbox_v4.py). Enforces sandbox_enforced=True or
raises RuntimeError.

Two execution modes:

- Foreground (default): blocks the tool call up to timeout_sec (max 120s).
  The child runs in its own process group; on timeout the WHOLE group gets
  SIGKILL (a bare proc.kill() would orphan grandchildren like the `find`
  spawned by `bash -c`).
- Background (run_in_background=true): returns immediately with a job_id.
  Output streams to <workspace>/tasks/<job_id>.log; the job lands in
  ctx.jobs (kind="shell") and shares the build-verb process table so
  check_job / wait_for_job / kill_job all work on it. Completion is
  announced by the session watcher — the model does NOT need to poll.

Dispatcher signature: async def dispatch(args: dict, ctx: ToolContext) -> dict.
Foreground returns {exit_code, stdout_tail, stderr_tail, timed_out,
sandbox_enforced, workspace_dir}; background returns {job_id, status,
log_path, sandbox_enforced, timeout_sec, summary}.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from gemia.sandbox_v4 import build_v4_sandbox_command, is_sandbox_disabled
from gemia.tools._context import ToolContext

_FOREGROUND_MAX_SEC = 120.0
_FOREGROUND_DEFAULT_SEC = 30.0
_BACKGROUND_MAX_SEC = 3600.0
_BACKGROUND_DEFAULT_SEC = 600.0
_MAX_PENDING_SHELL_JOBS = 3
_TAIL_CHARS = 4000


def _minimal_env() -> dict[str, str]:
    """Return a minimal environment: only PATH, HOME, TMPDIR, LANG.

    Avoids leaking secrets like OPENROUTER_API_KEY, GEMINI_API_KEY, etc.
    into the subprocess.
    """
    env = {}
    for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    # Ensure basic paths exist
    if "PATH" not in env:
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if "HOME" not in env:
        env["HOME"] = str(Path.home())
    return env


def _build_sandboxed_cmd(command: str, ctx: ToolContext) -> tuple[list[str], bool]:
    """Wrap `command` for execution: (argv, sandbox_enforced).

    When the user has explicitly disabled the sandbox (POST /settings/sandbox),
    run the raw command with full system access and report enforced=False
    honestly — do NOT raise.
    """
    if is_sandbox_disabled():
        return ["/bin/bash", "-c", command], False
    cmd, enforced = build_v4_sandbox_command(
        ["/bin/bash", "-c", command],
        workspace_dir=ctx.output_dir,
    )
    if not enforced:
        raise RuntimeError(
            "sandbox-exec unavailable or failed on this host; refusing to run "
            "command without sandbox enforcement"
        )
    return cmd, enforced


def _run_foreground(
    cmd: list[str], timeout_sec: float, cwd: str, env: dict[str, str]
) -> tuple[int, str, str, bool]:
    """Blocking run with a hard timeout that kills the whole process group.

    Returns (exit_code, stdout, stderr, timed_out); 124 on timeout, matching
    the coreutils `timeout` convention.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,  # child leads its own group → killpg reaches grandchildren
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        return proc.returncode, stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        # Second communicate() reaps the child and drains whatever partial
        # output made it into the pipes before the kill.
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        return 124, stdout or "", stderr or "", True


def _submit_background(
    command: str, cmd: list[str], enforced: bool, timeout_sec: float, ctx: ToolContext
) -> dict[str, Any]:
    """Start `cmd` detached and return immediately with a job_id.

    The Popen handle goes into build._PROCESSES (shared table — check_job,
    wait_for_job and kill_job already poll it); the JobRegistry record
    carries pid/pgid for kill/cleanup after the handle is gone.
    """
    # Function-level import: build.py imports _minimal_env from this module
    # at top level, so the reverse edge must be deferred.
    from gemia.tools import build as _build

    # Count only shell jobs whose OS process is still ALIVE — mirror the build
    # cap (build.py). Registry status lags reality: it flips to done/failed only
    # when the 2s watcher (or a model check_job) polls, so counting
    # list_pending() would wrongly reject a fresh submit while earlier quick
    # jobs have already exited but not yet been polled.
    # Snapshot the shared table before polling: proc.poll() calls os.waitpid,
    # which releases the GIL, and another session's watcher may pop from the
    # same module-global dict mid-iteration ("dictionary changed size during
    # iteration"). list(...) copies the view first — same guard as build.py's
    # atexit sweep.
    pending_shell = [
        jid
        for jid, (proc, _deadline) in list(_build._PROCESSES.items())
        if jid.startswith("shell_") and proc.poll() is None
    ]
    if len(pending_shell) >= _MAX_PENDING_SHELL_JOBS:
        ids = ", ".join(pending_shell)
        raise ValueError(
            f"too many pending background shell jobs ({len(pending_shell)}/"
            f"{_MAX_PENDING_SHELL_JOBS}: {ids}); wait for one to finish or "
            "kill_job it first"
        )

    record = ctx.jobs.submit(
        kind="shell",
        provider="local:bash-sandbox" if enforced else "local:bash-raw",
        operation_name="pending",
        pending_asset_id="-",
        estimated_eta_sec=timeout_sec,
        summary=command[:80],
    )
    job_id = record.job_id

    tasks_dir = Path(ctx.output_dir) / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    log_path = tasks_dir / f"{job_id}.log"

    try:
        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(ctx.output_dir),
                env=_minimal_env(),
                start_new_session=True,
            )
    except Exception as exc:
        ctx.jobs.update_from_poll(job_id, "failed", error=f"failed to start: {exc}")
        raise RuntimeError(f"failed to start background command: {exc}") from exc

    _build._PROCESSES[job_id] = (proc, time.monotonic() + timeout_sec)
    record.operation_name = str(proc.pid)
    record.pid = proc.pid
    record.pgid = proc.pid  # start_new_session=True → child is its own group leader
    record.started_epoch = time.time()

    on_background_job = ctx.extra.get("on_background_job")
    if callable(on_background_job):
        try:
            on_background_job(job_id)
        except Exception:
            pass  # watcher wiring is host-side plumbing; never fail the tool call

    result: dict[str, Any] = {
        "job_id": job_id,
        "status": "submitted",
        "log_path": str(log_path),
        "sandbox_enforced": enforced,
        "timeout_sec": timeout_sec,
        "summary": record.summary,
        "note": (
            "Running in background. You will be notified when it finishes — "
            "continue other work or end the turn; use check_job(job_id, "
            "since_offset) for incremental output or kill_job(job_id) to stop it."
        ),
    }
    if not enforced:
        result["warning"] = (
            "sandbox disabled: this background command has full system and "
            "network access and its resource usage is not metered"
        )
    return result


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run a bash command in an isolated sandbox.

    Args:
        command: required, bash command string
        timeout_sec: optional, seconds (foreground: default 30, max 120;
            background: default 600, max 3600)
        run_in_background: optional bool — return a job_id immediately
            instead of blocking the turn

    Foreground returns:
        {
            exit_code: int (124 if timed out),
            stdout_tail: str (last ~4000 chars),
            stderr_tail: str (last ~4000 chars),
            timed_out: bool,
            sandbox_enforced: bool,
            workspace_dir: str (absolute path),
        }
    """
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("run_shell requires a non-empty 'command' argument")

    run_in_background = bool(args.get("run_in_background") or False)
    default_sec = _BACKGROUND_DEFAULT_SEC if run_in_background else _FOREGROUND_DEFAULT_SEC
    max_sec = _BACKGROUND_MAX_SEC if run_in_background else _FOREGROUND_MAX_SEC

    timeout_sec = args.get("timeout_sec", default_sec)
    try:
        timeout_sec = float(timeout_sec)
    except (TypeError, ValueError):
        raise ValueError(f"timeout_sec must be a number, got {timeout_sec!r}") from None

    if timeout_sec <= 0 or timeout_sec > max_sec:
        raise ValueError(f"timeout_sec must be in (0, {int(max_sec)}], got {timeout_sec}")

    cmd, enforced = _build_sandboxed_cmd(command, ctx)

    if run_in_background:
        return _submit_background(command, cmd, enforced, timeout_sec, ctx)

    exit_code, stdout_data, stderr_data, timed_out = await asyncio.to_thread(
        _run_foreground, cmd, timeout_sec, str(ctx.output_dir), _minimal_env()
    )

    return {
        "exit_code": exit_code,
        "stdout_tail": stdout_data[-_TAIL_CHARS:],
        "stderr_tail": stderr_data[-_TAIL_CHARS:],
        "timed_out": timed_out,
        "sandbox_enforced": enforced,
        "workspace_dir": str(ctx.output_dir),
    }


__all__ = ["dispatch"]
