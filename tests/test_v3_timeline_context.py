"""M2 regression: the v3 loop owns a persistent timeline document.

Covers the session wiring only (ProjectHandle + prompt injection + SSE
``timeline_op`` event + undo). Op-vocabulary semantics live in
``test_timeline_patches.py``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.project_store import ProjectHandle
from gemia.tools._context import AssetRegistry, ToolContext


def _make_loop(tmp_path: Path, events: list[dict[str, Any]]) -> AgentLoopV3:
    return AgentLoopV3(
        session_id="v3-timelinectx01",
        output_dir=tmp_path,
        gemini_client=SimpleNamespace(model="fake-model"),  # type: ignore[arg-type]
        emit_event=events.append,
    )


def _legacy_insert_op(clip_id: str = "clip_m2a", start: float = 0.0) -> dict[str, Any]:
    return {
        "op": "insert_clip",
        "data": {
            "asset": {
                "id": "v_001",
                "name": "probe.mp4",
                "media_kind": "video",
                "source_path": "/nonexistent/probe.mp4",
                "duration": 4.0,
            },
            "clip": {
                "id": clip_id,
                "asset_id": "v_001",
                "track_id": "V1",
                "media_kind": "video",
                "start": start,
                "duration": 4.0,
                "source_in": 0.0,
                "source_out": 4.0,
            },
        },
    }


def test_loop_creates_project_and_injects_timeline_into_prompt(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    loop = _make_loop(tmp_path, events)

    assert (tmp_path / "project" / "v3-timelinectx01" / "state.json").exists()

    system = loop.render_messages()[0]["content"]
    assert "{{timeline}}" not in system
    assert "clips=0" in system


def test_apply_ops_emits_timeline_op_event_and_updates_prompt(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    loop = _make_loop(tmp_path, events)

    result = loop.project.apply_ops([_legacy_insert_op()], label="m2-test")

    assert result["patch_seq_end"] == 1
    timeline_events = [e for e in events if e.get("kind") == "timeline_op"]
    assert len(timeline_events) == 1
    assert timeline_events[0]["ops"] == ["insert_clip"]
    assert timeline_events[0]["clip_count"] == 1
    assert timeline_events[0]["seq"] == 1

    system = loop.render_messages()[0]["content"]
    assert "clip_m2a" in system
    assert "clips=1" in system


def test_undo_rewinds_last_patch(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    loop = _make_loop(tmp_path, events)
    loop.project.apply_ops([_legacy_insert_op()])

    undo = loop.project.undo(1)

    assert undo["to_seq"] == 0
    state = loop.project.load()
    assert state["timeline"]["clips"] == []
    system = loop.render_messages()[0]["content"]
    assert "clips=0" in system


def test_handle_reopen_sees_persisted_state(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    loop = _make_loop(tmp_path, events)
    loop.project.apply_ops([_legacy_insert_op()])

    reopened = ProjectHandle.open(
        tmp_path / "project", "v3-timelinectx01", session_id="v3-timelinectx01"
    )
    state = reopened.load()
    assert len(state["timeline"]["clips"]) == 1


def test_handle_open_sanitizes_bad_project_id(tmp_path: Path) -> None:
    handle = ProjectHandle.open(tmp_path, "bad id/with spaces", session_id="s")
    assert handle.project_id.startswith("p_")
    assert handle.load()["timeline"]["clips"] == []


def test_tool_context_without_project_stays_valid(tmp_path: Path) -> None:
    ctx = ToolContext(
        session_id="legacy",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )
    assert ctx.project is None
