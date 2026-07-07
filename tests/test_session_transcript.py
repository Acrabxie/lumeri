"""Durable session transcript (P3 durability, phase 1).

Every agent-emitted event is appended to
``<sessions_root>/<sid>/transcript.jsonl`` before the SSE fan-out. Unlike the
200-event SSE ring buffer, the transcript survives process restarts and
session close — GET /sessions/{id}/transcript serves it even when no runner
exists anymore.
"""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gemia import v3_routes
from gemia.session_manager import SessionRunner


def _bare_runner(tmp_path: Path, sid: str = "tr-test") -> SessionRunner:
    """A SessionRunner shell with just the transcript machinery wired (no
    agent thread, no credentials)."""
    r = SessionRunner.__new__(SessionRunner)
    r.session_id = sid
    r.sessions_root = tmp_path / "sessions"
    r._transcript_lock = threading.Lock()
    r._transcript_seq = 0
    r._transcript_file = None
    r._transcript_failed = False
    r._transcript_path = r.sessions_root / sid / "transcript.jsonl"
    return r


class _GetHandler:
    def __init__(self, path: str = "/") -> None:
        self.path = path
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key.lower()] = value

    def end_headers(self) -> None:
        pass


def test_events_append_in_order_with_seq_and_reach_sse(tmp_path, monkeypatch) -> None:
    r = _bare_runner(tmp_path)
    fanned: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "gemia.session_manager.SSE_REGISTRY",
        SimpleNamespace(emit=lambda sid, ev: fanned.append(ev)),
    )

    r._emit_event({"kind": "turn_start"})
    r._emit_event({"kind": "model_text_delta", "delta": "你好"})
    r._emit_event({"kind": "turn_complete", "asset_ids": []})

    lines = r._transcript_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(l) for l in lines]
    assert [p["seq"] for p in parsed] == [1, 2, 3]
    assert [p["event"]["kind"] for p in parsed] == [
        "turn_start", "model_text_delta", "turn_complete",
    ]
    assert parsed[1]["event"]["delta"] == "你好"
    assert all(isinstance(p["ts"], float) for p in parsed)
    # SSE fan-out saw every event too, same order.
    assert [e["kind"] for e in fanned] == [p["event"]["kind"] for p in parsed]


def test_transcript_failure_never_blocks_sse(tmp_path, monkeypatch) -> None:
    r = _bare_runner(tmp_path)
    fanned: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "gemia.session_manager.SSE_REGISTRY",
        SimpleNamespace(emit=lambda sid, ev: fanned.append(ev)),
    )
    # Force the append to fail: transcript path is a DIRECTORY.
    r._transcript_path.parent.mkdir(parents=True, exist_ok=True)
    r._transcript_path.mkdir()

    r._emit_event({"kind": "turn_start"})
    r._emit_event({"kind": "turn_complete"})

    assert r._transcript_failed is True
    assert [e["kind"] for e in fanned] == ["turn_start", "turn_complete"]


def test_route_serves_transcript_even_without_runner(tmp_path, monkeypatch) -> None:
    """The durability contract: after close (or a server restart), the file
    still serves."""
    sid = "tr-closed"
    r = _bare_runner(tmp_path, sid)
    monkeypatch.setattr(
        "gemia.session_manager.SSE_REGISTRY",
        SimpleNamespace(emit=lambda *_a: None),
    )
    for i in range(5):
        r._emit_event({"kind": "model_text_delta", "delta": str(i)})

    manager = SimpleNamespace(sessions_root=tmp_path / "sessions")
    monkeypatch.setattr(v3_routes, "get_manager", lambda: manager)

    h = _GetHandler()
    assert v3_routes._session_transcript(h, sid, {}, body=True) is True
    assert h.status == 200
    assert "ndjson" in h.response_headers["content-type"]
    lines = h.wfile.getvalue().decode("utf-8").splitlines()
    assert len(lines) == 5

    # Incremental catch-up.
    h2 = _GetHandler()
    v3_routes._session_transcript(h2, sid, {"since_seq": ["3"]}, body=True)
    tail = [json.loads(l) for l in h2.wfile.getvalue().decode("utf-8").splitlines()]
    assert [p["seq"] for p in tail] == [4, 5]


def test_route_rejects_bad_ids_and_missing_transcripts(tmp_path, monkeypatch) -> None:
    manager = SimpleNamespace(sessions_root=tmp_path / "sessions")
    monkeypatch.setattr(v3_routes, "get_manager", lambda: manager)

    h = _GetHandler()
    v3_routes._session_transcript(h, "../../etc", {}, body=True)
    assert h.status == 400

    h2 = _GetHandler()
    v3_routes._session_transcript(h2, "no-such-session", {}, body=True)
    assert h2.status == 404

    h3 = _GetHandler()
    (tmp_path / "sessions" / "s1").mkdir(parents=True)
    (tmp_path / "sessions" / "s1" / "transcript.jsonl").write_text("", encoding="utf-8")
    v3_routes._session_transcript(h3, "s1", {"since_seq": ["abc"]}, body=True)
    assert h3.status == 400
