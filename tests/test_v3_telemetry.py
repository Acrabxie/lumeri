"""Smoke tests for gemia.session_telemetry.

SQLite is written to a tmp path. No real agent loop is constructed; we
drive observe_event() directly with synthesized event dicts of the same
shape agent_loop_v3 emits today.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gemia import session_telemetry as st


def _records(db: Path, table: str) -> list[dict]:
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]


def test_turn_lifecycle_records_prompt_and_complete_status(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="s1", db_path=db, enabled=True)
    rec.begin_turn(prompt="trim the first 5 seconds", asset_count_at_start=1)
    rec.observe_event({"kind": "model_text_delta", "delta": "好的,我"})
    rec.observe_event({"kind": "model_text_delta", "delta": "来裁剪"})
    rec.observe_event(
        {
            "kind": "tool_exec_result",
            "call_id": "c1",
            "tool_name": "edit_video",
            "result": {"asset_id": "v_002", "kind": "video"},
            "elapsed_seconds": 0.42,
        }
    )
    rec.observe_event(
        {
            "kind": "turn_complete",
            "deliverable_asset_ids": ["v_002"],
            "final_asset_ids": ["v_002"],
        }
    )
    # End-block end_turn (status='crashed') must NOT overwrite the
    # turn_complete that observe_event already finalized.
    rec.end_turn(status="crashed", asset_count_at_end=2)

    turns = _records(db, "turns")
    assert len(turns) == 1
    t = turns[0]
    assert t["prompt"] == "trim the first 5 seconds"
    assert t["status"] == "complete"
    assert t["model_text"] == "好的,我来裁剪"
    assert json.loads(t["deliverable_asset_ids_json"]) == ["v_002"]


def test_tool_exec_error_records_error_class(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="s2", db_path=db, enabled=True)
    rec.begin_turn(prompt="do something v3 can't do")
    rec.observe_event(
        {
            "kind": "tool_exec_error",
            "call_id": "c1",
            "tool_name": "generate_video",
            "error": "NotImplementedError: tool 'generate_video' is not implemented: needs provider client",
            "elapsed_seconds": 0.001,
        }
    )
    rec.observe_event({"kind": "turn_complete", "deliverable_asset_ids": []})
    rec.end_turn(status="crashed")

    calls = _records(db, "tool_calls")
    assert len(calls) == 1
    c = calls[0]
    assert c["status"] == "error"
    assert c["tool_name"] == "generate_video"
    assert c["error_class"] == "NotImplementedError"
    assert "needs provider client" in c["error_message"]


def test_list_unimplemented_complaints_returns_stub_calls(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="s3", db_path=db, enabled=True)
    rec.begin_turn(prompt="please make me a Veo clip of a cat")
    rec.observe_event(
        {
            "kind": "tool_exec_error",
            "call_id": "c1",
            "tool_name": "generate_video",
            "error": "NotImplementedError: deferred to batch 2",
        }
    )
    rec.observe_event({"kind": "turn_complete", "deliverable_asset_ids": []})

    complaints = st.list_unimplemented_complaints(db_path=db, since_days=30)
    assert len(complaints) == 1
    assert complaints[0]["tool_name"] == "generate_video"
    assert complaints[0]["prompt"] == "please make me a Veo clip of a cat"


def test_disabled_telemetry_is_no_op(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="s4", db_path=db, enabled=False)
    rec.begin_turn(prompt="anything")
    rec.observe_event({"kind": "tool_exec_result", "tool_name": "edit_video"})
    rec.end_turn(status="complete")
    assert not db.exists()


def test_env_var_disables_telemetry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMIA_V3_TELEMETRY_DISABLED", "1")
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="s5", db_path=db)
    assert rec.enabled is False
    rec.begin_turn(prompt="x")
    rec.end_turn(status="complete")
    assert not db.exists()


def test_concurrent_turns_share_correct_status(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    s1 = st.SessionTelemetry(session_id="alpha", db_path=db, enabled=True)
    s2 = st.SessionTelemetry(session_id="bravo", db_path=db, enabled=True)
    s1.begin_turn(prompt="first")
    s2.begin_turn(prompt="second")
    s1.observe_event({"kind": "turn_error", "error": "boom"})
    s2.observe_event({"kind": "turn_complete", "deliverable_asset_ids": ["v_001"]})
    s1.end_turn(status="crashed")
    s2.end_turn(status="crashed")

    turns = _records(db, "turns")
    assert {t["session_id"]: t["status"] for t in turns} == {
        "alpha": "error",
        "bravo": "complete",
    }


def test_turn_summary_aggregates_by_tool(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.sqlite3"
    rec = st.SessionTelemetry(session_id="agg", db_path=db, enabled=True)
    rec.begin_turn(prompt="multi")
    for _ in range(3):
        rec.observe_event(
            {
                "kind": "tool_exec_result",
                "call_id": "c",
                "tool_name": "edit_video",
                "result": {"asset_id": "v_001"},
            }
        )
    rec.observe_event(
        {
            "kind": "tool_exec_error",
            "call_id": "cE",
            "tool_name": "generate_image",
            "error": "NotImplementedError: stub",
        }
    )
    rec.observe_event({"kind": "turn_complete", "deliverable_asset_ids": ["v_001"]})

    summary = st.turn_summary(db_path=db, since_days=30)
    assert summary["turn_count"] == 1
    by_name = {
        (r["tool_name"], r["status"]): r["n"]
        for r in summary["tool_calls_by_name_and_status"]
    }
    assert by_name[("edit_video", "ok")] == 3
    assert by_name[("generate_image", "error")] == 1
