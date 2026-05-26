"""SSE transport for the Lumeri v3 agent loop.

Invariants (grep-verifiable):

1. ``tool_exec_progress`` events MUST come from a real progress callback
   inside a tool dispatcher (FFmpeg stderr ``out_time=`` parsing, Veo job
   poll, etc.). The host never synthesizes progress with sleep + fake
   percentages. A tool that has no real progress channel (e.g.
   ``analyze_media``) emits only ``tool_exec_start`` and
   ``tool_exec_result``; the frontend renders an indeterminate spinner
   in between. That is honest reporting, not a defect.

2. ``model_text_delta`` events MUST come from a real Gemini stream
   chunk. The host never synthesizes "thinking...", "executing...", or
   any other status narration. If the model is silent, the frontend
   stays silent.

This module owns per-session in-process queues. Producers (the agent
loop running in a worker thread) call ``emit`` from any thread. The SSE
HTTP handler (in server.py) calls ``iter_events`` from its request
thread and writes chunks to the response until ``close`` is signaled.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Iterator


_SENTINEL: Any = object()


class SseSessionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, queue.Queue[Any]] = {}

    def register(self, session_id: str) -> queue.Queue[Any]:
        with self._lock:
            if session_id in self._queues:
                raise ValueError(f"session already registered: {session_id}")
            q: queue.Queue[Any] = queue.Queue()
            self._queues[session_id] = q
            return q

    def is_registered(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._queues

    def emit(self, session_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            q = self._queues.get(session_id)
        if q is None:
            return
        q.put(event)

    def close(self, session_id: str) -> None:
        with self._lock:
            q = self._queues.get(session_id)
        if q is None:
            return
        q.put(_SENTINEL)

    def unregister(self, session_id: str) -> None:
        with self._lock:
            self._queues.pop(session_id, None)


REGISTRY = SseSessionRegistry()


def format_sse_chunk(event: dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


def iter_events(session_id: str) -> Iterator[bytes]:
    """Yield SSE-formatted bytes for a session until close() is signaled.

    Blocks on the session queue. Returns immediately if the session is
    not registered.
    """
    with REGISTRY._lock:  # noqa: SLF001
        q = REGISTRY._queues.get(session_id)
    if q is None:
        return
    while True:
        item = q.get()
        if item is _SENTINEL:
            return
        yield format_sse_chunk(item)


__all__ = ["REGISTRY", "SseSessionRegistry", "format_sse_chunk", "iter_events"]
