"""SessionManager + SessionRunner for the Lumeri v3 HTTP API.

Each session owns:
  - one ``AgentLoopV3`` instance (state: messages, registry, budget)
  - one background thread running a dedicated ``asyncio`` event loop
  - one SSE queue + replay buffer registered in
    ``gemia.transport.sse.REGISTRY`` under the same session_id

HTTP handler threads interact with a session by submitting coroutines
to its loop via ``asyncio.run_coroutine_threadsafe``. The loop runs
the agent and emits events to the SSE queue (which any thread can
emit to safely).

Multi-session: ``SessionManager`` holds a dict of runners. No
artificial single-session restriction. No fairness/rate-limiting in
M1 — that's a separate concern if real concurrency becomes a need.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.transport.sse import REGISTRY as SSE_REGISTRY


_DEFAULT_OUTPUT_ROOT = Path("/tmp/lumeri-v3")
_DEFAULT_MAX_SESSIONS = 20
_DEFAULT_IDLE_TIMEOUT_SEC = 2 * 60 * 60
_DEFAULT_SWEEP_INTERVAL_SEC = 60


class SessionLimitError(RuntimeError):
    """Raised when the process-wide v3 session cap has been reached."""


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        return default
    return value if value >= minimum else default


class SessionRunner:
    """Owns one AgentLoopV3 inside a dedicated thread + asyncio loop."""

    def __init__(
        self,
        *,
        session_id: str,
        output_dir: Path,
        sessions_root: Path,
        account_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.output_dir = Path(output_dir)
        self.sessions_root = Path(sessions_root)
        self.account_id = str(account_id or "").strip()

        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._turn_in_progress = False
        now = time.time()
        self.created_at = now
        self.last_used_at = now
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"lumeri-v3-{session_id}",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

        fut = asyncio.run_coroutine_threadsafe(self._create_agent(), self._loop)
        self.agent: AgentLoopV3 = fut.result(timeout=20)

    def touch(self) -> None:
        with self._state_lock:
            self.last_used_at = time.time()

    @property
    def turn_in_progress(self) -> bool:
        with self._state_lock:
            return self._turn_in_progress

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
            except Exception:
                pass
            self._loop.close()

    async def _create_agent(self) -> AgentLoopV3:
        return AgentLoopV3(
            session_id=self.session_id,
            output_dir=self.output_dir,
            sessions_root=self.sessions_root,
            extra={"account_id": self.account_id} if self.account_id else None,
        )

    def add_external_asset(self, path: Path, *, summary: str = "") -> str:
        self.touch()

        async def _add() -> str:
            return self.agent.add_external_asset(Path(path), summary=summary)

        fut = asyncio.run_coroutine_threadsafe(_add(), self._loop)
        return fut.result(timeout=30)

    def submit_turn(self, message: str) -> bool:
        """Fire-and-forget if no turn is active.

        Returns ``True`` when the turn was scheduled, or ``False`` when the
        session already has a turn running. The frontend disables the send
        button, but the HTTP layer needs this guard for direct/concurrent
        callers too.
        """

        with self._state_lock:
            if self._turn_in_progress:
                return False
            self._turn_in_progress = True
            self.last_used_at = time.time()

        async def _run() -> None:
            try:
                await self.agent.run_turn(message)
            finally:
                with self._state_lock:
                    self._turn_in_progress = False
                    self.last_used_at = time.time()

        asyncio.run_coroutine_threadsafe(_run(), self._loop)
        return True

    def deliver_ask_answer(self, question_id: str, answers: dict[str, Any]) -> bool:
        """Deliver a user's answer to a pending ``elicit`` question.

        Returns True if a matching pending question was found. The agent's bridge
        hops the resolution back onto this session's event loop, so this is safe to
        call directly from the HTTP handler thread.
        """
        self.touch()
        return self.agent.deliver_ask_answer(question_id, answers)

    def set_plan_mode(self, enabled: bool) -> bool:
        """Toggle the agent's plan mode. Safe from the HTTP handler thread
        (atomic bool flip + thread-safe SSE emit). Returns the new state."""
        self.touch()
        return self.agent.set_plan_mode(enabled)

    @property
    def plan_mode(self) -> bool:
        return bool(self.agent.plan_mode)

    def asset_path(self, asset_id: str) -> Path | None:
        self.touch()
        if not self.agent.registry.contains(asset_id):
            return None
        return self.agent.registry.get(asset_id).path

    def list_assets(self) -> list[dict[str, Any]]:
        self.touch()
        records = self.agent.registry.list_records()
        return [
            {
                "asset_id": r.asset_id,
                "kind": r.kind,
                "summary": r.summary,
                "created_at": r.created_at,
                "lineage": list(r.lineage),
            }
            for r in records
        ]

    def close(self) -> None:
        if self._loop.is_closed():
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._cancel_pending(), self._loop)
            fut.result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _cancel_pending(self) -> None:
        current = asyncio.current_task(self._loop)
        tasks = [t for t in asyncio.all_tasks(self._loop) if t is not current and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class SessionManager:
    """Process-wide directory of active v3 sessions."""

    def __init__(
        self,
        *,
        output_root: Path,
        max_sessions: int | None = None,
        idle_timeout_sec: int | None = None,
        sweep_interval_sec: int | None = None,
        cleanup_workdirs: bool | None = None,
    ) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._sessions_root = self._output_root / "sessions"
        self._workdirs_root = self._output_root / "workdirs"
        self._workdirs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._runners: dict[str, SessionRunner] = {}
        self._creating_sessions = 0
        self._max_sessions = max(
            1,
            int(
                max_sessions
                if max_sessions is not None
                else _env_int("LUMERI_V3_MAX_SESSIONS", _DEFAULT_MAX_SESSIONS, minimum=1)
            ),
        )
        self._idle_timeout_sec = max(
            0,
            int(
                idle_timeout_sec
                if idle_timeout_sec is not None
                else _env_int("LUMERI_V3_IDLE_TIMEOUT_SEC", _DEFAULT_IDLE_TIMEOUT_SEC, minimum=0)
            ),
        )
        self._sweep_interval_sec = max(
            0,
            int(
                sweep_interval_sec
                if sweep_interval_sec is not None
                else _env_int("LUMERI_V3_SWEEP_INTERVAL_SEC", _DEFAULT_SWEEP_INTERVAL_SEC, minimum=0)
            ),
        )
        self._cleanup_workdirs = (
            cleanup_workdirs
            if cleanup_workdirs is not None
            else os.environ.get("LUMERI_V3_KEEP_CLOSED_WORKDIRS") not in {"1", "true", "TRUE"}
        )
        self._stop_sweeper = threading.Event()
        self._sweeper: threading.Thread | None = None
        if self._sweep_interval_sec > 0:
            self._sweeper = threading.Thread(
                target=self._sweep_loop,
                name="lumeri-v3-session-sweeper",
                daemon=True,
            )
            self._sweeper.start()

    def create_session(self, *, account_id: str | None = None) -> SessionRunner:
        self.cleanup_idle()
        session_id = f"v3-{uuid.uuid4().hex[:12]}"
        with self._lock:
            active_or_creating = len(self._runners) + self._creating_sessions
            if active_or_creating >= self._max_sessions:
                raise SessionLimitError(
                    f"too many active v3 sessions ({active_or_creating} >= {self._max_sessions})"
                )
            self._creating_sessions += 1
        # Register SSE BEFORE the agent thread starts so the agent's
        # first emit (turn_start) isn't dropped.
        runner: SessionRunner | None = None
        registered = False
        created = False
        try:
            SSE_REGISTRY.register(session_id)
            registered = True
            runner = SessionRunner(
                session_id=session_id,
                output_dir=self._workdirs_root / session_id,
                sessions_root=self._sessions_root,
                account_id=account_id,
            )
            created = True
        except Exception:
            if runner is not None:
                runner.close()
            if registered:
                SSE_REGISTRY.close(session_id)
                SSE_REGISTRY.unregister(session_id)
            raise
        finally:
            with self._lock:
                self._creating_sessions -= 1
                if created and runner is not None:
                    self._runners[session_id] = runner
        assert runner is not None
        return runner

    def get(self, session_id: str) -> SessionRunner | None:
        with self._lock:
            runner = self._runners.get(session_id)
        if runner is not None:
            runner.touch()
        return runner

    def list_sessions(self) -> list[str]:
        with self._lock:
            return sorted(self._runners.keys())

    def close_session(self, session_id: str, *, remove_workdir: bool = False) -> None:
        with self._lock:
            runner = self._runners.pop(session_id, None)
        if runner is None:
            return
        try:
            runner.close()
        finally:
            SSE_REGISTRY.close(session_id)
            SSE_REGISTRY.unregister(session_id)
            if remove_workdir:
                self._remove_workdir(runner.output_dir)

    def cleanup_idle(self) -> list[str]:
        if self._idle_timeout_sec <= 0:
            return []
        now = time.time()
        expired: list[str] = []
        with self._lock:
            for sid, runner in self._runners.items():
                if runner.turn_in_progress:
                    continue
                if now - runner.last_used_at >= self._idle_timeout_sec:
                    expired.append(sid)
        for sid in expired:
            self.close_session(sid, remove_workdir=self._cleanup_workdirs)
        return expired

    def close_all(self, *, remove_workdirs: bool = False) -> None:
        with self._lock:
            session_ids = list(self._runners.keys())
        for sid in session_ids:
            self.close_session(sid, remove_workdir=remove_workdirs)
        self._stop_sweeper.set()

    def _sweep_loop(self) -> None:
        while not self._stop_sweeper.wait(self._sweep_interval_sec):
            self.cleanup_idle()

    def _remove_workdir(self, path: Path) -> None:
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self._workdirs_root.resolve())
        except Exception:
            return
        shutil.rmtree(resolved, ignore_errors=True)

    @property
    def output_root(self) -> Path:
        return self._output_root


_SINGLETON_LOCK = threading.Lock()
_SINGLETON: SessionManager | None = None


def get_manager() -> SessionManager:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            root = Path(os.environ.get("LUMERI_V3_OUTPUT_ROOT") or _DEFAULT_OUTPUT_ROOT)
            _SINGLETON = SessionManager(output_root=root)
        return _SINGLETON


__all__ = ["SessionLimitError", "SessionManager", "SessionRunner", "get_manager"]
