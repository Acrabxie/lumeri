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
   any other status narration. If the model is silent, the user-facing
   stream stays silent.

This module owns per-session in-process queues. Producers (the agent
loop running in a worker thread) call ``emit`` from any thread. The SSE
HTTP handler (in server.py) calls ``iter_events`` from its request
thread and writes chunks to the response until ``close`` is signaled.

Each emit assigns a monotonic per-session ``event_id``. A bounded ring
buffer (``REPLAY_BUFFER_SIZE`` events) keeps recent events so a
reconnecting client with ``Last-Event-ID`` can replay everything it
missed before resuming the live stream. The buffer is per-session; old
events are discarded once the buffer is full.
"""
from __future__ import annotations

import json
import queue
import threading
from collections import deque
from typing import Any, Iterator


REPLAY_BUFFER_SIZE = 200

_SENTINEL: Any = object()


class _SessionState:
    __slots__ = ("queue", "buffer", "next_id", "closed")

    def __init__(self) -> None:
        self.queue: queue.Queue[Any] = queue.Queue()
        self.buffer: deque[tuple[int, dict[str, Any]]] = deque(maxlen=REPLAY_BUFFER_SIZE)
        self.next_id: int = 1
        self.closed: bool = False


class SseSessionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _SessionState] = {}

    def register(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._states:
                raise ValueError(f"session already registered: {session_id}")
            self._states[session_id] = _SessionState()

    def is_registered(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._states

    def emit(self, session_id: str, event: dict[str, Any]) -> int | None:
        """Emit one event, return its assigned event_id (or None if not registered)."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None or state.closed:
                return None
            event_id = state.next_id
            state.next_id += 1
            state.buffer.append((event_id, dict(event)))
            state.queue.put((event_id, event))
        return event_id

    def close(self, session_id: str) -> None:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return
            state.closed = True
            state.queue.put(_SENTINEL)

    def unregister(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id, None)

    def replay_from(self, session_id: str, last_event_id: int) -> list[tuple[int, dict[str, Any]]]:
        """Return all buffered events with id > last_event_id, oldest first."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return []
            return [(eid, ev) for (eid, ev) in state.buffer if eid > last_event_id]

    def latest_event_id(self, session_id: str) -> int | None:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None
            return state.next_id - 1 if state.next_id > 1 else 0

    def _get_state(self, session_id: str) -> _SessionState | None:
        with self._lock:
            return self._states.get(session_id)


REGISTRY = SseSessionRegistry()


def format_sse_chunk(event_id: int, event: dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\ndata: {payload}\n\n".encode("utf-8")


def iter_events(session_id: str, *, last_event_id: int | None = None) -> Iterator[bytes]:
    """Yield SSE-formatted bytes for a session until close() is signaled.

    If ``last_event_id`` is provided, first replays every buffered event
    with id greater than ``last_event_id`` (in order), then resumes
    live streaming from the queue. The same event may be re-delivered
    in the live stream if it arrived in the buffer between the replay
    snapshot and the live stream attach — the client should de-dupe by
    event_id, but this is rare in practice because the buffer snapshot
    and the queue attach happen under the same lock.
    """
    state = REGISTRY._get_state(session_id)  # noqa: SLF001
    if state is None:
        return

    cutoff = int(last_event_id) if last_event_id is not None else 0
    delivered: set[int] = set()
    if last_event_id is not None:
        for eid, ev in REGISTRY.replay_from(session_id, cutoff):
            delivered.add(eid)
            yield format_sse_chunk(eid, ev)

    while True:
        item = state.queue.get()
        if item is _SENTINEL:
            return
        eid, ev = item
        # Skip events the client already has: either acknowledged via
        # Last-Event-ID, or just replayed from the buffer above.
        if eid <= cutoff or eid in delivered:
            continue
        yield format_sse_chunk(eid, ev)


__all__ = [
    "REGISTRY",
    "REPLAY_BUFFER_SIZE",
    "SseSessionRegistry",
    "format_sse_chunk",
    "iter_events",
]
