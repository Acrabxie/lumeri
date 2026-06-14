"""M4 regression: project_export verb — full-quality multi-track composition.

Tests cover: single video clip, multi-clip concatenation, image overlay,
text overlay, empty-project error, and ripple-then-export round-trip.
All tests use real ffmpeg (testsrc2 / lavfi), real ProjectStore on disk,
and the DISPATCHER verb layer — the same path the agent loop uses.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


# ── fixtures ─────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, session: str = "m4-test-01") -> ToolContext:
    registry = AssetRegistry()
    handle = ProjectHandle.open(tmp_path / "project", f"m4{session}", session_id=session)
    return ToolContext(
        session_id=session,
        output_dir=tmp_path,
        registry=registry,
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _gen_video(tmp_path: Path, name: str = "clip.mp4", duration: float = 2.0) -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc2=duration={duration}:size=128x128:rate=15",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ],
        capture_output=True,
        check=True,
    )
    return out


def _gen_image(tmp_path: Path, name: str = "overlay.png") -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=red:s=64x32:d=1",
            "-frames:v", "1", str(out),
        ],
        capture_output=True,
        check=True,
    )
    return out


def _register_video(ctx: ToolContext, path: Path) -> str:
    rec = ctx.registry.add_external(path, summary="test clip")
    return rec.asset_id


def _register_image(ctx: ToolContext, path: Path) -> str:
    rec = ctx.registry.add_external(path, summary="test image")
    return rec.asset_id


def _clips(ctx: ToolContext) -> list[dict[str, Any]]:
    return ctx.project.load()["timeline"]["clips"]


# ── test cases ────────────────────────────────────────────────────────────────


def test_project_export_single_clip(tmp_path: Path) -> None:
    """Single video clip → export produces an MP4 with positive duration."""
    ctx = _make_ctx(tmp_path, "single")
    vid = _gen_video(tmp_path)
    aid = _register_video(ctx, vid)
    _call("timeline_insert_clip", {"asset_id": aid}, ctx)

    out = _call("project_export", {"quality": "draft", "label": "single"}, ctx)

    assert out["asset_id"] is not None
    assert out["duration"] > 0.0
    assert out["video_clips"] == 1
    assert out["overlay_clips"] == 0
    assert Path(out["export_path"]).exists()
    assert Path(out["export_path"]).stat().st_size > 0


def test_project_export_multi_clip_concatenation(tmp_path: Path) -> None:
    """Two sequential video clips → export duration ≈ sum of clip durations."""
    ctx = _make_ctx(tmp_path, "multi")
    v1 = _gen_video(tmp_path, "c1.mp4", duration=1.5)
    v2 = _gen_video(tmp_path, "c2.mp4", duration=1.0)
    a1 = _register_video(ctx, v1)
    a2 = _register_video(ctx, v2)
    _call("timeline_insert_clip", {"asset_id": a1}, ctx)
    _call("timeline_insert_clip", {"asset_id": a2}, ctx)

    out = _call("project_export", {"quality": "draft", "label": "multi"}, ctx)

    assert out["video_clips"] == 2
    assert out["duration"] > 2.0  # both clips rendered


def test_project_export_with_image_overlay(tmp_path: Path) -> None:
    """Video + image overlay clip → export succeeds and overlay_clips > 0."""
    ctx = _make_ctx(tmp_path, "imgov")
    vid = _gen_video(tmp_path, "base.mp4", duration=3.0)
    img = _gen_image(tmp_path)
    aid = _register_video(ctx, vid)
    iid = _register_image(ctx, img)
    _call("timeline_insert_clip", {"asset_id": aid}, ctx)

    # Insert image overlay on OV1 track at t=0.5 for 1 second
    clips_before = _clips(ctx)
    v_clip_id = clips_before[0]["id"]
    # Add overlay via low-level apply_ops (simulating what insert_clip does for overlay):
    from lumerai.patches import apply_timeline_patches
    state = ctx.project.load()
    # find OV1 track
    ov_tracks = [t for t in state["timeline"]["tracks"] if t.get("kind") == "overlay"]
    if not ov_tracks:
        # create one
        _call("timeline_add_track", {"kind": "overlay"}, ctx)
        state = ctx.project.load()
        ov_tracks = [t for t in state["timeline"]["tracks"] if t.get("kind") == "overlay"]
    ov_track_id = ov_tracks[0]["id"]

    ctx.project.apply_ops(
        [
            {
                "op": "insert_clip",
                "track_id": ov_track_id,
                "at": {"time": 0.5},
                "data": {
                    "clip": {
                        "id": "ov_img_001",
                        "asset_id": iid,
                        "media_kind": "image",
                        "start": 0.5,
                        "duration": 1.0,
                        "source_in": 0.0,
                        "source_out": 3.0,
                        "enabled": True,
                        "effects": {"x": 10, "y": 10, "scale": 0.5},
                    }
                },
            }
        ],
        label="test-overlay-insert",
    )

    out = _call("project_export", {"quality": "draft", "label": "imgov"}, ctx)

    assert out["video_clips"] == 1
    assert out["overlay_clips"] == 1
    assert Path(out["export_path"]).exists()
    assert Path(out["export_path"]).stat().st_size > 0


def test_project_export_with_text_overlay(tmp_path: Path) -> None:
    """Video + text clip → export succeeds and overlay_clips > 0."""
    ctx = _make_ctx(tmp_path, "textov")
    vid = _gen_video(tmp_path, "base.mp4", duration=3.0)
    aid = _register_video(ctx, vid)
    _call("timeline_insert_clip", {"asset_id": aid}, ctx)

    state = ctx.project.load()
    ov_tracks = [t for t in state["timeline"]["tracks"] if t.get("kind") == "overlay"]
    if not ov_tracks:
        _call("timeline_add_track", {"kind": "overlay"}, ctx)
        state = ctx.project.load()
        ov_tracks = [t for t in state["timeline"]["tracks"] if t.get("kind") == "overlay"]
    ov_track_id = ov_tracks[0]["id"]

    ctx.project.apply_ops(
        [
            {
                "op": "insert_clip",
                "track_id": ov_track_id,
                "at": {"time": 0.0},
                "data": {
                    "clip": {
                        "id": "ov_txt_001",
                        "asset_id": "text_placeholder",
                        "media_kind": "text",
                        "start": 0.0,
                        "duration": 2.0,
                        "source_in": 0.0,
                        "source_out": 2.0,
                        "enabled": True,
                        "text_config": {
                            "content": "Hello World",
                            "font_size": 48.0,
                            "color": "#ffffff",
                            "align": "center",
                        },
                        "effects": {"x": 64, "y": 100},
                    }
                },
            }
        ],
        label="test-text-insert",
    )

    out = _call("project_export", {"quality": "draft", "label": "textov"}, ctx)

    assert out["video_clips"] == 1
    assert out["overlay_clips"] == 1
    assert out["duration"] > 0.0
    assert Path(out["export_path"]).exists()


def test_project_export_empty_project_raises(tmp_path: Path) -> None:
    """Exporting a project with no video clips must raise an error."""
    from gemia.project_export import ProjectExportError

    ctx = _make_ctx(tmp_path, "empty")
    with pytest.raises((ProjectExportError, Exception)) as exc_info:
        _call("project_export", {"quality": "draft"}, ctx)
    # error code should indicate no video clips
    err_str = str(exc_info.value).lower()
    assert "no" in err_str or "empty" in err_str or "clip" in err_str


def test_project_export_after_ripple_delete(tmp_path: Path) -> None:
    """Insert 3 clips, delete middle with ripple=True, export the remaining 2."""
    ctx = _make_ctx(tmp_path, "ripple")
    v1 = _gen_video(tmp_path, "r1.mp4", duration=1.0)
    v2 = _gen_video(tmp_path, "r2.mp4", duration=1.0)
    v3 = _gen_video(tmp_path, "r3.mp4", duration=1.0)
    a1 = _register_video(ctx, v1)
    a2 = _register_video(ctx, v2)
    a3 = _register_video(ctx, v3)

    r1 = _call("timeline_insert_clip", {"asset_id": a1}, ctx)
    r2 = _call("timeline_insert_clip", {"asset_id": a2}, ctx)
    r3 = _call("timeline_insert_clip", {"asset_id": a3}, ctx)
    mid_id = r2["clip_id"]

    _call("timeline_delete_clip", {"clip_id": mid_id, "ripple": True}, ctx)
    remaining = _clips(ctx)
    assert len(remaining) == 2

    out = _call("project_export", {"quality": "draft", "label": "ripple"}, ctx)

    assert out["video_clips"] == 2
    assert out["duration"] > 1.5  # 2 clips ~2 s total
    assert Path(out["export_path"]).exists()
