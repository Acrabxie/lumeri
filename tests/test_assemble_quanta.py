from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from gemia.budget_guard import BudgetGuard
from gemia.plan_mode import is_plan_safe
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import assemble_quanta as assemble_tool


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "quanta-assemble", session_id="session_1")
    return ToolContext(
        session_id="session_1", output_dir=tmp_path, registry=AssetRegistry(),
        emit_progress=lambda _update: None, project=handle,
    )


def _call(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _quanta() -> dict[str, Any]:
    return {
        "slides": [
            {"id": "s1", "layout": "content", "title": "", "blocks": [
                {"id": "a", "kind": "shape", "role": "accent"},
            ], "builds": [
                {"id": "b1", "dwell_sec": 1, "visible_block_ids": []},
                {"id": "b2", "dwell_sec": 2, "visible_block_ids": ["a"]},
            ]},
            {"id": "s2", "layout": "content", "title": "", "blocks": [
                {"id": "b", "kind": "shape", "role": "accent"},
            ], "builds": [
                {"id": "b1", "dwell_sec": 3, "visible_block_ids": ["b"]},
            ]},
        ],
        "default_path": ["s2", "s1"],
    }


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 9), "#5fc6de").save(output, format="PNG")
    return output.getvalue()


def _install_fakes(monkeypatch):
    calls = {"materialize": 0, "black": 0}

    def fake_materialize(quanta, ctx, **_kwargs):
        calls["materialize"] += 1
        asset_ids = []
        frames = []
        specs = [("s2", "s2_b1", 3.0), ("s1", "s1_b1", 1.0), ("s1", "s1_b2", 2.0)]
        for index, (scope_id, state_id, dwell) in enumerate(specs):
            asset_id = ctx.registry.allocate_id("image")
            path = ctx.child_path(asset_id, ".png")
            path.write_bytes(_png_bytes())
            ctx.registry.register_output(
                asset_id, kind="image", path=path, summary="quanta frame"
            )
            asset_ids.append(asset_id)
            frames.append({
                "scope_index": 0 if scope_id == "s2" else 1,
                "state_index": 0 if state_id.endswith("_b1") else 1,
                "scope_id": scope_id, "state_id": state_id,
                "dwell_sec": dwell, "asset_id": asset_id,
                "source_asset_ids": [], "overflow": [],
            })
        return {
            "kind": "quanta", "asset_id": asset_ids[0], "frame_asset_ids": asset_ids,
            "frames": frames, "pager_url": "/video/quanta.html?session_id=session_1",
            "first_state_pager_url": "/video/quanta.html?session_id=session_1",
            "scope_count": 2, "frame_count": 3, "overflow": [],
            "summary": "rendered",
        }

    async def fake_black(ctx, **_kwargs):
        cached = ctx.extra.get("quanta_black_video_cache")
        if isinstance(cached, dict) and cached.get("key") == _kwargs["cache_key"]:
            cached_id = str(cached.get("asset_id") or "")
            if cached_id and ctx.registry.contains(cached_id):
                return cached_id
        calls["black"] += 1
        asset_id = ctx.registry.allocate_id("video")
        path = ctx.child_path(asset_id, ".mp4")
        path.write_bytes(b"fake video")
        ctx.registry.register_output(
            asset_id, kind="video", path=path, summary="quanta background"
        )
        ctx.extra["quanta_black_video_cache"] = {
            "key": _kwargs["cache_key"], "asset_id": asset_id,
        }
        return asset_id

    monkeypatch.setattr(assemble_tool, "materialize_quanta_frame_assets", fake_materialize)
    monkeypatch.setattr(assemble_tool, "_ensure_black_video", fake_black)
    return calls


def test_assemble_quanta_atomically_rebuilds_dedicated_tracks_and_reuses_cache(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _quanta()}, ctx)
    calls = _install_fakes(monkeypatch)

    first = _call("assemble_quanta", {}, ctx)
    assert first["assembled"] is True and first["total_duration_sec"] == 6.0
    assert first["frame_count"] == 3 and first["degradations"] == []
    state = ctx.project.load()
    tracks = {track["id"]: track for track in state["timeline"]["tracks"]}
    assert tracks[assemble_tool.QUANTA_VIDEO_TRACK]["kind"] == "video"
    assert tracks[assemble_tool.QUANTA_FRAME_TRACK]["kind"] == "overlay"
    clips = state["timeline"]["clips"]
    background = [clip for clip in clips if clip["track_id"] == assemble_tool.QUANTA_VIDEO_TRACK]
    frames = [clip for clip in clips if clip["track_id"] == assemble_tool.QUANTA_FRAME_TRACK]
    assert len(background) == 1 and background[0]["duration"] == 6.0
    assert [(clip["start"], clip["duration"]) for clip in frames] == [
        (0.0, 3.0), (3.0, 1.0), (4.0, 2.0),
    ]
    assert [clip["provenance"]["scope_id"] for clip in frames] == ["s2", "s1", "s1"]
    first_frame_ids = list(first["frame_asset_ids"])
    asset_count = len(state["assets"])

    second = _call("assemble_quanta", {}, ctx)
    state2 = ctx.project.load()
    assert second["frame_asset_ids"] == first_frame_ids
    assert calls == {"materialize": 1, "black": 1}
    assert len(state2["timeline"]["clips"]) == 4
    assert len(state2["assets"]) == asset_count
    assert [clip["id"] for clip in state2["timeline"]["clips"]] == second["clip_ids"]


def test_assemble_quanta_preserves_unrelated_timeline_clips(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _quanta()}, ctx)
    unrelated = tmp_path / "unrelated.png"
    unrelated.write_bytes(_png_bytes())
    asset_id = ctx.registry.add_external(unrelated).asset_id
    inserted = _call("timeline_insert_clip", {"asset_id": asset_id, "duration": 9}, ctx)
    calls = _install_fakes(monkeypatch)

    _call("assemble_quanta", {}, ctx)
    clips = ctx.project.load()["timeline"]["clips"]
    assert any(clip["id"] == inserted["clip_id"] and clip["track_id"] == "OV1" for clip in clips)
    assert calls["materialize"] == 1


def test_assemble_quanta_empty_quanta_is_actionable(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="quanta is empty"):
        _call("assemble_quanta", {}, ctx)


def test_assemble_quanta_is_registered_blocked_in_plan_and_budgeted() -> None:
    assert "assemble_quanta" in DISPATCHER
    assert "stub" not in DISPATCHER["assemble_quanta"].__qualname__.lower()
    assert is_plan_safe("assemble_quanta") is False
    decision = BudgetGuard(max_usd=1, max_seconds=100).check("assemble_quanta")
    assert decision.ok is True and decision.estimated_eta_sec == 12.0
