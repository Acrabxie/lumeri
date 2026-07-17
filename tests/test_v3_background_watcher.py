"""Background-job watcher, completion-notice injection, and auto-resume.

Covers the Slice 2 event-driven half of the background-task chain:

  * poll_background_jobs advances a real job to terminal, emits a
    background_task_update SSE, budget-commits the wall-clock ONCE, and queues
    exactly one completion notice — extra polls after finalization are no-ops;
  * run_background_resume_turn injects the queued notice as a synthetic
    role:"user" message and drives a real turn; an empty queue is a no-op;
  * _drain_background_notifications renders every queued notice then clears;
  * identical repeated check_job polls do NOT trip the doom-loop guard (they
    are exempt) — with a control that proves they WOULD without the exemption;
  * SessionRunner auto-resume gating (rate limit / min-interval / fast-fail /
    cap / disabled) never double-starts a turn;
  * a full SessionRunner integration: submit a background job → the watcher
    polls it done → auto-resumes an idle session → the notice reaches the
    conversation.

Real jobs run raw bash (sandbox disabled); the model is a fake client so no
network is touched. build._PROCESSES is reaped after every test.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

import gemia.agent_loop_v3 as agent_loop_v3
from gemia import session_manager
from gemia.agent_loop_v3 import AgentLoopV3
from gemia.sandbox_v4 import is_sandbox_disabled, set_sandbox_disabled
from gemia.session_manager import SessionRunner, _BG_RESUME_MAX_PER_HOUR
from gemia.tools import build, run_shell


# ── fake model clients ───────────────────────────────────────────────────────


class _TextStopClient:
    """Always yields one text delta then stops. Counts stream_turn calls so a
    test can assert the model was (or wasn't) driven."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        yield {"kind": "text_delta", "text": "ack"}
        yield {"kind": "finish", "reason": "stop"}


class _CallsCheckJobNTimes:
    """Calls check_job(job_id) with BYTE-IDENTICAL args N times, then finishes
    with text. Mirrors a model politely polling a long background job."""

    model = "fake"

    def __init__(self, job_id: str, n: int) -> None:
        self.calls = 0
        self._args = json.dumps({"job_id": job_id})
        self._n = n

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls <= self._n:
            yield {"kind": "tool_call_start", "index": 0,
                   "id": f"c{self.calls}", "name": "check_job"}
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": self._args}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        yield {"kind": "text_delta", "text": "still running; moving on"}
        yield {"kind": "finish", "reason": "stop"}


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_sandbox():
    was = is_sandbox_disabled()
    set_sandbox_disabled(True)
    yield
    set_sandbox_disabled(was)


@pytest.fixture(autouse=True)
def _clean_processes():
    yield
    import os
    import signal

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


def _make_loop(tmp_path: Path, client: Any | None = None):
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="bg_watch",
        output_dir=tmp_path,
        gemini_client=client or _TextStopClient(),
        emit_event=events.append,
    )
    return loop, events


# ── poll_background_jobs ─────────────────────────────────────────────────────


def test_poll_background_jobs_emits_commits_and_queues_once(tmp_path: Path) -> None:
    loop, events = _make_loop(tmp_path)
    submit = asyncio.run(
        run_shell.dispatch(
            {"command": "printf DONE", "run_in_background": True}, loop._tool_ctx
        )
    )
    job_id = submit["job_id"]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        loop.poll_background_jobs()
        if job_id in loop._bg_finalized:
            break
        time.sleep(0.05)
    assert job_id in loop._bg_finalized, "job never reached a terminal state"

    updates = [
        e for e in events
        if e.get("kind") == "background_task_update" and e.get("job_id") == job_id
    ]
    assert updates, "no background_task_update SSE emitted"
    assert updates[-1]["status"] == "done"
    assert updates[-1]["exit_code"] == 0

    assert job_id in loop._bg_committed  # wall-clock committed exactly once
    assert loop.has_pending_background_notifications()

    # Extra polls after finalization neither re-emit nor re-queue.
    before = len(events)
    loop.poll_background_jobs()
    assert len(events) == before

    note = loop._drain_background_notifications()
    assert note is not None
    assert job_id in note
    assert "exit 0" in note
    assert "printf DONE" in note
    assert not loop.has_pending_background_notifications()


# ── run_background_resume_turn ───────────────────────────────────────────────


def test_run_background_resume_turn_injects_notice_and_drives_a_turn(tmp_path: Path) -> None:
    client = _TextStopClient()
    loop, events = _make_loop(tmp_path, client)
    loop.queue_background_notification(
        {
            "job_id": "shell_abc",
            "status": "done",
            "exit_code": 0,
            "summary": "find ~ -name '*.mov'",
            "elapsed_sec": 12.0,
            "output_tail": "/a.mov\n/b.mov",
        }
    )

    ran = asyncio.run(loop.run_background_resume_turn())
    assert ran is True

    user_msgs = [m for m in loop._messages if m.get("role") == "user"]
    assert any("[background job update" in str(m.get("content")) for m in user_msgs)
    assert any("shell_abc" in str(m.get("content")) for m in user_msgs)
    assert client.calls >= 1
    assert [e for e in events if e.get("kind") == "turn_complete"]

    # Queue now empty → a second resume is a no-op (a concurrent turn may have
    # already drained it in production).
    assert asyncio.run(loop.run_background_resume_turn()) is False


def test_drain_renders_all_queued_notices_then_clears(tmp_path: Path) -> None:
    loop, _ = _make_loop(tmp_path)
    loop.queue_background_notification(
        {"job_id": "shell_1", "status": "done", "exit_code": 0, "summary": "cmd one"}
    )
    loop.queue_background_notification(
        {
            "job_id": "shell_2",
            "status": "failed",
            "exit_code": 1,
            "summary": "cmd two",
            "error": "boom",
        }
    )
    note = loop._drain_background_notifications()
    assert note is not None
    assert "host notice, not user input" in note
    assert "shell_1" in note and "shell_2" in note
    assert "cmd one" in note and "cmd two" in note
    assert "boom" in note
    assert loop._drain_background_notifications() is None


# ── doom-loop exemption ──────────────────────────────────────────────────────


def test_identical_check_job_polls_do_not_trip_doom_loop(tmp_path: Path) -> None:
    """Four byte-identical check_job polls on a live job are legal waiting, not
    an echo loop: check_job is exempt, so the guard never fires and the turn
    completes."""
    loop, events = _make_loop(tmp_path)
    submit = asyncio.run(
        run_shell.dispatch(
            {"command": "sleep 5", "run_in_background": True}, loop._tool_ctx
        )
    )
    job_id = submit["job_id"]
    loop.client = _CallsCheckJobNTimes(job_id, 4)

    asyncio.run(loop.run_turn("watch that job"))

    assert not [e for e in events if e.get("reason") == "doom_loop"]
    assert not [e for e in events if e.get("kind") == "turn_error"]
    assert [e for e in events if e.get("kind") == "turn_complete"]
    # All four polls actually dispatched (were not gated/aborted).
    check_results = [
        e for e in events
        if e.get("kind") == "tool_exec_result" and e.get("tool_name") == "check_job"
    ]
    assert len(check_results) == 4
    assert loop.client.calls >= 5  # 4 poll turns + at least one closing text turn

    asyncio.run(build.dispatch_kill({"job_id": job_id}, loop._tool_ctx))


def test_without_the_exemption_identical_check_job_would_trip(tmp_path: Path, monkeypatch) -> None:
    """Control: remove check_job from the exempt set and the SAME identical
    polls trip the doom-loop guard at exactly the threshold — proving the
    exemption (not some other quirk) is what keeps polling legal."""
    monkeypatch.setattr(agent_loop_v3, "_DOOM_LOOP_EXEMPT_TOOLS", frozenset())
    loop, events = _make_loop(tmp_path)
    submit = asyncio.run(
        run_shell.dispatch(
            {"command": "sleep 5", "run_in_background": True}, loop._tool_ctx
        )
    )
    job_id = submit["job_id"]
    loop.client = _CallsCheckJobNTimes(job_id, 4)

    asyncio.run(loop.run_turn("watch that job"))

    assert [e for e in events if e.get("reason") == "doom_loop"]
    assert loop.client.calls == agent_loop_v3._DOOM_LOOP_THRESHOLD == 3

    asyncio.run(build.dispatch_kill({"job_id": job_id}, loop._tool_ctx))


# ── auto-resume rate-limit gating (bare SessionRunner) ───────────────────────


def _bare_runner() -> SessionRunner:
    """A SessionRunner with only the auto-resume bookkeeping wired — no thread,
    no loop, no agent. Enough to exercise the pure gating logic."""
    r = SessionRunner.__new__(SessionRunner)
    r._state_lock = threading.Lock()
    r._turn_in_progress = False
    r._bg_resume_times = []
    r._bg_last_resume_at = 0.0
    r.last_used_at = time.time()
    return r


def test_resume_capped_prunes_old_and_caps_recent() -> None:
    r = _bare_runner()
    assert r._resume_capped() is False

    now = time.time()
    r._bg_resume_times = [now - 5 for _ in range(_BG_RESUME_MAX_PER_HOUR)]
    assert r._resume_capped() is True

    # Entries older than an hour are pruned, dropping back under the cap.
    r._bg_resume_times = [now - 4000 for _ in range(_BG_RESUME_MAX_PER_HOUR)]
    assert r._resume_capped() is False
    assert r._bg_resume_times == []


def test_auto_resume_gated_when_turn_in_progress() -> None:
    r = _bare_runner()
    r._turn_in_progress = True
    r._auto_resume(False)
    assert r._turn_in_progress is True  # unchanged; no second turn scheduled
    assert r._bg_resume_times == []


def test_auto_resume_gated_within_min_interval() -> None:
    r = _bare_runner()
    r._bg_last_resume_at = time.time()  # just resumed
    r._auto_resume(False)
    assert r._turn_in_progress is False  # 10s min-interval not yet elapsed
    assert r._bg_resume_times == []


def test_auto_resume_fast_fail_uses_longer_backoff() -> None:
    r = _bare_runner()
    r._bg_last_resume_at = time.time() - 15  # 15s ago
    # Normal 10s interval would allow, but a fast-fail uses the 30s backoff.
    r._auto_resume(True)
    assert r._turn_in_progress is False
    assert r._bg_resume_times == []


def test_auto_resume_gated_when_capped() -> None:
    r = _bare_runner()
    now = time.time()
    r._bg_resume_times = [now - 5 for _ in range(_BG_RESUME_MAX_PER_HOUR)]
    r._auto_resume(False)
    assert r._turn_in_progress is False


def test_auto_resume_gated_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("LUMERI_BG_AUTORESUME", "0")
    r = _bare_runner()
    r._auto_resume(False)
    assert r._turn_in_progress is False
    assert r._bg_resume_times == []


# ── watcher survives a turn's tail window (regression) ───────────────────────


class _NotifyStubAgent:
    """Minimal agent stand-in for _bg_watch: no jobs pending, a completion
    notice queued until the (simulated) resume turn drains it."""

    def __init__(self) -> None:
        self._notifs = True

    def poll_background_jobs(self):
        return {"pending": 0, "had_fast_fail": False}

    def has_pending_background_notifications(self) -> bool:
        return self._notifs


def test_watcher_survives_turn_tail_window_then_auto_resumes(tmp_path, monkeypatch) -> None:
    """Regression for the tail-window drop: a background job that finishes while
    a turn is composing its FINAL (no-tool) response leaves a notice queued but
    the turn never loops back to drain it. The watcher must NOT exit while the
    turn holds the flag — it has to survive and auto-resume the session once the
    turn ends idle. Before the fix, _bg_watch returned immediately on
    turn_in_progress and the notice sat undelivered until the next user turn."""
    monkeypatch.setenv("LUMERI_BG_AUTORESUME", "1")
    monkeypatch.setattr(session_manager, "_BG_WATCH_INTERVAL_SEC", 0.02)

    r = _bare_runner()
    r._turn_in_progress = True  # a user turn is still running
    r.agent = _NotifyStubAgent()
    resume_calls: list[bool] = []
    r._auto_resume = lambda fast_fail: resume_calls.append(fast_fail)  # type: ignore[method-assign]

    async def _drive() -> None:
        watch = asyncio.ensure_future(r._bg_watch())
        await asyncio.sleep(0.12)  # several ticks while the turn is in progress
        assert not watch.done(), "watcher exited while a turn held a queued notice"
        assert resume_calls == [], "auto-resume fired while a turn was in progress"

        r._turn_in_progress = False  # turn ends → session idle
        await asyncio.sleep(0.12)
        assert resume_calls, "watcher did not auto-resume the now-idle session"

        # The resume turn drains the notice → the watcher may now exit.
        r.agent._notifs = False
        await asyncio.wait_for(watch, timeout=1.0)

    asyncio.run(_drive())


# ── full SessionRunner integration ───────────────────────────────────────────


class _AgentWithFakeClient(AgentLoopV3):
    """AgentLoopV3 that never touches the network — the watcher/auto-resume
    plumbing is what we're testing, not the model."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["gemini_client"] = _TextStopClient()
        super().__init__(**kwargs)


def test_session_runner_watcher_auto_resumes_on_completion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_manager, "AgentLoopV3", _AgentWithFakeClient)
    monkeypatch.setenv("LUMERI_BG_AUTORESUME", "1")

    runner = SessionRunner(
        session_id="bg_integ",
        output_dir=tmp_path / "wd",
        sessions_root=tmp_path / "sessions",
    )
    try:
        ctx = runner.agent._tool_ctx
        # Submit a quick background job on the runner's own loop, exactly as the
        # run_shell dispatcher does — this fires on_background_job → watcher.
        fut = asyncio.run_coroutine_threadsafe(
            run_shell.dispatch(
                {"command": "printf HELLO", "run_in_background": True}, ctx
            ),
            runner._loop,
        )
        submit = fut.result(timeout=10)
        job_id = submit["job_id"]

        # The watcher polls the job done and auto-resumes an idle session; wait
        # for the fake model to actually be driven.
        client = runner.agent.client
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.calls >= 1:
                break
            time.sleep(0.1)
        assert client.calls >= 1, "watcher never auto-resumed a turn"

        time.sleep(0.3)  # let the resume turn settle before reading messages
        user_msgs = [m for m in runner.agent._messages if m.get("role") == "user"]
        assert any("[background job update" in str(m.get("content")) for m in user_msgs)
        assert any(job_id in str(m.get("content")) for m in user_msgs)
        assert job_id in runner.agent._bg_committed
    finally:
        runner.close()
