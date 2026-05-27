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
import threading
import uuid
from pathlib import Path
from typing import Any

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.transport.sse import REGISTRY as SSE_REGISTRY


_DEFAULT_OUTPUT_ROOT = Path("/tmp/lumeri-v3")


class SessionRunner:
    """Owns one AgentLoopV3 inside a dedicated thread + asyncio loop."""

    def __init__(
        self,
        *,
        session_id: str,
        output_dir: Path,
        sessions_root: Path,
    ) -> None:
        self.session_id = session_id
        self.output_dir = Path(output_dir)
        self.sessions_root = Path(sessions_root)

        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"lumeri-v3-{session_id}",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

        fut = asyncio.run_coroutine_threadsafe(self._create_agent(), self._loop)
        self.agent: AgentLoopV3 = fut.result(timeout=20)

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
        )

    def add_external_asset(self, path: Path, *, summary: str = "") -> str:
        async def _add() -> str:
            return self.agent.add_external_asset(Path(path), summary=summary)

        fut = asyncio.run_coroutine_threadsafe(_add(), self._loop)
        return fut.result(timeout=30)

    def submit_turn(self, message: str) -> None:
        """Fire-and-forget. Returns as soon as the coroutine is scheduled."""

        async def _run() -> None:
            await self.agent.run_turn(message)

        asyncio.run_coroutine_threadsafe(_run(), self._loop)

    def asset_path(self, asset_id: str) -> Path | None:
        if not self.agent.registry.contains(asset_id):
            return None
        return self.agent.registry.get(asset_id).path

    def list_assets(self) -> list[dict[str, Any]]:
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
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class SessionManager:
    """Process-wide directory of active v3 sessions."""

    def __init__(self, *, output_root: Path) -> None:
        self._output_root = Path(output_root).expanduser().resolve()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._sessions_root = self._output_root / "sessions"
        self._workdirs_root = self._output_root / "workdirs"
        self._workdirs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._runners: dict[str, SessionRunner] = {}

    def create_session(self) -> SessionRunner:
        session_id = f"v3-{uuid.uuid4().hex[:12]}"
        # Register SSE BEFORE the agent thread starts so the agent's
        # first emit (turn_start) isn't dropped.
        SSE_REGISTRY.register(session_id)
        try:
            runner = SessionRunner(
                session_id=session_id,
                output_dir=self._workdirs_root / session_id,
                sessions_root=self._sessions_root,
            )
        except Exception:
            SSE_REGISTRY.unregister(session_id)
            raise
        with self._lock:
            self._runners[session_id] = runner
        return runner

    def get(self, session_id: str) -> SessionRunner | None:
        with self._lock:
            return self._runners.get(session_id)

    def list_sessions(self) -> list[str]:
        with self._lock:
            return sorted(self._runners.keys())

    def close_session(self, session_id: str) -> None:
        with self._lock:
            runner = self._runners.pop(session_id, None)
        if runner is None:
            return
        try:
            runner.close()
        finally:
            SSE_REGISTRY.close(session_id)
            SSE_REGISTRY.unregister(session_id)

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


__all__ = ["SessionManager", "SessionRunner", "get_manager"]
