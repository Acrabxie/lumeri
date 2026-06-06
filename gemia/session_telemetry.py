"""Minimal session telemetry for v3.

What this records, and why:

This is data collection for v4 build prioritization. v4 will let the model
write Python in a sandbox instead of picking from canned verbs. To know
which prompts justify v4 build (vs which are already well-served by the
canned set), we need a real record of:

- what users asked for (the prompt),
- which verbs the model reached for,
- which calls succeeded vs failed (and why),
- what the model said back to the user (so "I can't do X" / "no verb for
  this" / "做不到" signals are recoverable by grep later — we deliberately
  do NOT classify at write time).

The SQLite + WAL + single-file selection follows the v2 skill_telemetry
choice (gemia/ai/skill_telemetry.py). Thread-safe via short-lived
connections per write; the agent loop is multi-threaded across sessions.

Privacy: prompts and model text are stored verbatim. No PII filter. This
is local single-user data. Do not ship the SQLite file off-machine.

Schema is migration-friendly: only additive ALTER TABLE on bump. Two
tables today:

    turns(
        session_id, turn_index, prompt, started_at, ended_at, status,
        model_text, asset_count_delta, ...
    )
    tool_calls(
        session_id, turn_index, call_index, tool_name, args_json, status,
        error_class, error_message, elapsed_sec, produced_asset_id, ts
    )

The recorder is intentionally fire-and-forget: any write failure is
swallowed (with a warning to stderr) so telemetry never blocks the loop.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_DB_ENV = "GEMIA_V3_TELEMETRY_DB"
_DEFAULT_DISABLED_ENV = "GEMIA_V3_TELEMETRY_DISABLED"
_SCHEMA_VERSION = 1

_INIT_LOCK = threading.Lock()
_INIT_DONE: set[str] = set()


def default_db_path() -> Path:
    override = os.environ.get(_DEFAULT_DB_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemia" / "v3" / "telemetry.sqlite3"


def telemetry_disabled() -> bool:
    return os.environ.get(_DEFAULT_DISABLED_ENV, "").strip().lower() in {"1", "true", "yes"}


def _init_db(path: Path) -> None:
    key = str(path)
    if key in _INIT_DONE:
        return
    with _INIT_LOCK:
        if key in _INIT_DONE:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT,
                    model_text TEXT,
                    asset_count_delta INTEGER DEFAULT 0,
                    deliverable_asset_ids_json TEXT,
                    UNIQUE(session_id, turn_index)
                );
                CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    call_index INTEGER NOT NULL,
                    call_id TEXT,
                    tool_name TEXT NOT NULL,
                    args_json TEXT,
                    status TEXT NOT NULL,
                    error_class TEXT,
                    error_message TEXT,
                    elapsed_sec REAL,
                    produced_asset_id TEXT,
                    ts TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_name);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            conn.commit()
        _INIT_DONE.add(key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _TurnState:
    turn_index: int
    prompt: str
    started_at: str
    started_mono: float
    model_text_buf: list[str] = field(default_factory=list)
    call_counter: int = 0
    asset_count_at_start: int = 0
    deliverable_ids: list[str] = field(default_factory=list)
    seen_turn_error: bool = False
    seen_turn_complete: bool = False
    finalized: bool = False


class SessionTelemetry:
    """Thread-safe per-session recorder. One instance per AgentLoopV3."""

    def __init__(
        self,
        *,
        session_id: str,
        db_path: str | Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self.db_path = Path(db_path) if db_path else default_db_path()
        if enabled is None:
            self.enabled = not telemetry_disabled()
        else:
            self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._current: _TurnState | None = None
        self._turn_index_counter = 0
        if self.enabled:
            try:
                _init_db(self.db_path)
            except Exception as exc:  # pragma: no cover - defensive
                self.enabled = False
                _warn(f"telemetry init failed; disabling: {exc}")

    # ── turn lifecycle ────────────────────────────────────────────────

    def begin_turn(self, *, prompt: str, asset_count_at_start: int = 0) -> int:
        if not self.enabled:
            return -1
        with self._lock:
            self._turn_index_counter += 1
            turn = _TurnState(
                turn_index=self._turn_index_counter,
                prompt=str(prompt or ""),
                started_at=_now(),
                started_mono=time.monotonic(),
                asset_count_at_start=int(asset_count_at_start),
            )
            self._current = turn
            self._insert_turn_row(turn)
            return turn.turn_index

    def end_turn(
        self,
        *,
        status: str,
        asset_count_at_end: int = 0,
        deliverable_asset_ids: list[str] | None = None,
    ) -> None:
        """Finalize the current turn. Idempotent — safe to call after the
        observe_event handler has already auto-finalized on turn_complete /
        turn_error. The caller's status only wins for the first call; later
        calls are no-ops so a finally-block 'crashed' status doesn't
        overwrite a real 'complete'."""
        if not self.enabled:
            return
        with self._lock:
            turn = self._current
            if turn is None or turn.finalized:
                return
            turn.finalized = True
            ended = _now()
            model_text = "".join(turn.model_text_buf)
            delta = max(0, int(asset_count_at_end) - turn.asset_count_at_start)
            effective_ids = deliverable_asset_ids if deliverable_asset_ids else turn.deliverable_ids
            self._update_turn_row(
                turn_index=turn.turn_index,
                ended_at=ended,
                status=status,
                model_text=model_text,
                asset_count_delta=delta,
                deliverable_asset_ids=effective_ids or [],
            )
            self._current = None

    # ── per-event hooks (called from agent_loop_v3._emit) ─────────────

    def observe_event(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("kind")
        if kind == "model_text_delta":
            with self._lock:
                if self._current is not None:
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        self._current.model_text_buf.append(delta)
            return
        if kind in {"tool_exec_result", "tool_exec_error", "budget_gate"}:
            self._record_tool_call_from_event(event)
            return
        if kind == "turn_complete":
            ids_raw = event.get("deliverable_asset_ids") or event.get("final_asset_ids") or []
            ids = [str(x) for x in ids_raw if isinstance(x, (str, int))]
            with self._lock:
                if self._current is None:
                    return
                self._current.deliverable_ids = ids
                self._current.seen_turn_complete = True
            # Auto-finalize with status="complete" if the loop hasn't.
            self._auto_finalize("complete")
            return
        if kind == "turn_error":
            with self._lock:
                if self._current is None:
                    return
                self._current.seen_turn_error = True
            self._auto_finalize("error")
            return

    def _auto_finalize(self, status: str) -> None:
        with self._lock:
            turn = self._current
            if turn is None or turn.finalized:
                return
            turn.finalized = True
            ended = _now()
            model_text = "".join(turn.model_text_buf)
            self._update_turn_row(
                turn_index=turn.turn_index,
                ended_at=ended,
                status=status,
                model_text=model_text,
                asset_count_delta=0,
                deliverable_asset_ids=turn.deliverable_ids,
            )
            self._current = None

    def _record_tool_call_from_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            turn = self._current
            if turn is None:
                return
            turn.call_counter += 1
            call_index = turn.call_counter
        kind = event.get("kind")
        tool_name = str(event.get("tool_name") or "")
        call_id = event.get("call_id")
        elapsed = event.get("elapsed_seconds")
        try:
            elapsed_val: float | None = float(elapsed) if elapsed is not None else None
        except (TypeError, ValueError):
            elapsed_val = None
        produced_id: str | None = None
        status = "unknown"
        error_class: str | None = None
        error_message: str | None = None
        args_json: str | None = None

        if kind == "tool_exec_result":
            status = "ok"
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            aid = result.get("asset_id")
            if isinstance(aid, str):
                produced_id = aid
        elif kind == "tool_exec_error":
            status = "error"
            err = str(event.get("error") or "")
            # Pull "ErrorClass: message" if present.
            if ":" in err:
                error_class, _, error_message = err.partition(":")
                error_class = error_class.strip() or None
                error_message = (error_message or "").strip() or None
            else:
                error_message = err or None
        elif kind == "budget_gate":
            status = "budget_gate"
            error_message = str(event.get("reason") or "")

        self._insert_tool_call(
            turn_index=self._current.turn_index if self._current else 0,
            call_index=call_index,
            call_id=str(call_id) if call_id else None,
            tool_name=tool_name,
            args_json=args_json,
            status=status,
            error_class=error_class,
            error_message=error_message,
            elapsed_sec=elapsed_val,
            produced_asset_id=produced_id,
        )

    def record_tool_call_args(
        self, *, call_id: str | None, tool_name: str, args: Any
    ) -> None:
        """Optional explicit hook to log model-supplied args at dispatch time.

        Not called from observe_event because args may be large. Callers can
        invoke this from agent_loop_v3 if/when args are interesting; today the
        event log only captures errors + results.
        """
        if not self.enabled:
            return
        try:
            args_json = json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args_json = None
        with self._lock:
            turn_index = self._current.turn_index if self._current else 0
        self._insert_tool_call(
            turn_index=turn_index,
            call_index=-1,  # marker for "args-only pre-dispatch" entry
            call_id=str(call_id) if call_id else None,
            tool_name=str(tool_name or ""),
            args_json=args_json,
            status="dispatching",
            error_class=None,
            error_message=None,
            elapsed_sec=None,
            produced_asset_id=None,
        )

    # ── SQL ─────────────────────────────────────────────────────────────

    def _insert_turn_row(self, turn: _TurnState) -> None:
        if not self.enabled:
            return
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO turns(
                        session_id, turn_index, prompt, started_at, status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.session_id,
                        turn.turn_index,
                        turn.prompt,
                        turn.started_at,
                        "in_progress",
                    ),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover - never block the loop
            _warn(f"telemetry begin_turn write failed: {exc}")

    def _update_turn_row(
        self,
        *,
        turn_index: int,
        ended_at: str,
        status: str,
        model_text: str,
        asset_count_delta: int,
        deliverable_asset_ids: list[str],
    ) -> None:
        try:
            payload = json.dumps(deliverable_asset_ids, ensure_ascii=False)
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    UPDATE turns
                    SET ended_at = ?, status = ?, model_text = ?,
                        asset_count_delta = ?, deliverable_asset_ids_json = ?
                    WHERE session_id = ? AND turn_index = ?
                    """,
                    (
                        ended_at,
                        status,
                        model_text,
                        asset_count_delta,
                        payload,
                        self.session_id,
                        turn_index,
                    ),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover
            _warn(f"telemetry end_turn write failed: {exc}")

    def _insert_tool_call(
        self,
        *,
        turn_index: int,
        call_index: int,
        call_id: str | None,
        tool_name: str,
        args_json: str | None,
        status: str,
        error_class: str | None,
        error_message: str | None,
        elapsed_sec: float | None,
        produced_asset_id: str | None,
    ) -> None:
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    INSERT INTO tool_calls(
                        session_id, turn_index, call_index, call_id, tool_name,
                        args_json, status, error_class, error_message,
                        elapsed_sec, produced_asset_id, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.session_id,
                        turn_index,
                        call_index,
                        call_id,
                        tool_name,
                        args_json,
                        status,
                        error_class,
                        error_message,
                        elapsed_sec,
                        produced_asset_id,
                        _now(),
                    ),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover
            _warn(f"telemetry tool_call write failed: {exc}")


def _warn(message: str) -> None:
    print(f"[session_telemetry] {message}", file=sys.stderr, flush=True)


# ── Read-side helpers (for inspection / v4 prioritization queries) ──


def list_unimplemented_complaints(
    *, since_days: int = 30, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Return tool_calls that hit NotImplementedError stubs, with the
    user prompt that led to them. The first cut at "what does v3 not cover".

    Read-only; safe to call from any thread.
    """
    path = Path(db_path) if db_path else default_db_path()
    if not path.exists():
        return []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT tc.tool_name, tc.error_message, tc.ts,
                   t.session_id, t.turn_index, t.prompt
            FROM tool_calls tc
            JOIN turns t
              ON t.session_id = tc.session_id AND t.turn_index = tc.turn_index
            WHERE tc.status = 'error'
              AND tc.error_class = 'NotImplementedError'
              AND tc.ts >= datetime('now', ?)
            ORDER BY tc.ts DESC
            """,
            (f"-{int(since_days)} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def turn_summary(
    *, since_days: int = 30, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Aggregate counts useful for v4 build planning."""
    path = Path(db_path) if db_path else default_db_path()
    if not path.exists():
        return {"db_missing": True}
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        ts_filter = f"-{int(since_days)} days"
        turn_count = conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE started_at >= datetime('now', ?)",
            (ts_filter,),
        ).fetchone()["n"]
        tool_rows = conn.execute(
            """
            SELECT tool_name, status, COUNT(*) AS n
            FROM tool_calls
            WHERE ts >= datetime('now', ?)
            GROUP BY tool_name, status
            ORDER BY n DESC
            """,
            (ts_filter,),
        ).fetchall()
        return {
            "turn_count": turn_count,
            "tool_calls_by_name_and_status": [dict(r) for r in tool_rows],
        }


__all__ = [
    "SessionTelemetry",
    "default_db_path",
    "telemetry_disabled",
    "list_unimplemented_complaints",
    "turn_summary",
]
