from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gemia.errors import ToolError
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER, TOOL_NAMES, TOOL_SCHEMAS
from gemia.tools._context import AssetRegistry, ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "paint-test", session_id="paint-test")
    return ToolContext(
        session_id="paint-test",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        project=handle,
    )


def _asset_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="paint-asset-test",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _call(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _make_image(path: Path) -> Path:
    im = Image.new("RGB", (64, 64), "#202020")
    im.save(path)
    return path


def _make_video(path: Path, *, duration: float = 1.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={duration}:r=10",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _pixel(path: Path, x: int, y: int) -> tuple[int, int, int, int]:
    with Image.open(path).convert("RGBA") as im:
        return im.getpixel((x, y))


def test_paint_tools_registered_and_schema_present() -> None:
    names = {tool["function"]["name"] for tool in TOOL_SCHEMAS}
    for name in ("paint_overlay", "paint_mask_effect"):
        assert name in TOOL_NAMES
        assert name in DISPATCHER
        assert name in names


def test_paint_overlay_generates_png_inserts_clip_and_undoes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    out = _call(
        "paint_overlay",
        {
            "shape": "rect",
            "rect": [0.1, 0.1, 0.3, 0.3],
            "color": "#ff0000",
            "width": 18,
            "duration": 2.0,
        },
        ctx,
    )

    assert out["clip_id"].startswith("clip_")
    assert out["track_id"] == "OV1"
    record = ctx.registry.get(out["asset_id"])
    assert record.kind == "image"
    assert record.path.exists()
    assert _pixel(record.path, 192, 108)[3] > 0

    clips = ctx.project.load()["timeline"]["clips"]
    assert len(clips) == 1
    assert clips[0]["name"] == "paint: rect"

    undo = _call("timeline_undo", {"steps": 1}, ctx)
    assert undo["applied"] is True
    assert ctx.project.load()["timeline"]["clips"] == []


def test_paint_overlay_rejects_out_of_bounds_points(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolError, match="points"):
        _call(
            "paint_overlay",
            {"shape": "stroke", "points": [[0.0, 0.0], [1.2, 0.5]]},
            ctx,
        )


def test_paint_mask_effect_highlights_image_region(tmp_path: Path) -> None:
    ctx = _asset_ctx(tmp_path)
    image_id = ctx.registry.add_external(_make_image(tmp_path / "base.png")).asset_id

    out = _call(
        "paint_mask_effect",
        {
            "asset_id": image_id,
            "effect": "highlight",
            "mask": {"shape": "rect", "rect": [0.0, 0.0, 0.5, 1.0]},
            "params": {"color": "#00ff00", "amount": 1.0},
        },
        ctx,
    )

    result = ctx.registry.get(out["asset_id"])
    assert result.kind == "image"
    left = _pixel(result.path, 12, 32)
    right = _pixel(result.path, 52, 32)
    assert left[1] > 220
    assert right[1] < 80
    assert ctx.registry.get(image_id).path.name == "base.png"


def test_paint_mask_effect_video_respects_time_window(tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")
    ctx = _asset_ctx(tmp_path)
    video_id = ctx.registry.add_external(_make_video(tmp_path / "base.mp4")).asset_id

    out = _call(
        "paint_mask_effect",
        {
            "asset_id": video_id,
            "effect": "highlight",
            "mask": {"shape": "rect", "rect": [0.0, 0.0, 1.0, 1.0]},
            "params": {"color": "#00ff00", "amount": 1.0},
            "start_sec": 0.5,
            "end_sec": 1.0,
        },
        ctx,
    )

    cap = cv2.VideoCapture(str(ctx.registry.get(out["asset_id"]).path))
    ok0, first = cap.read()
    cap.set(cv2.CAP_PROP_POS_MSEC, 700)
    ok1, later = cap.read()
    cap.release()
    assert ok0 and ok1
    assert int(first[32, 32, 1]) < 80
    assert int(later[32, 32, 1]) > 160


def test_inspect_timeline_sees_paint_overlay(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    video_id = ctx.registry.add_external(_make_video(tmp_path / "black.mp4")).asset_id
    _call("timeline_insert_clip", {"asset_id": video_id}, ctx)
    _call(
        "paint_overlay",
        {
            "shape": "highlight",
            "points": [[0.0, 0.0], [1.0, 1.0]],
            "color": "#00ff00",
            "fill_opacity": 1.0,
            "opacity": 1.0,
            "duration": 1.0,
        },
        ctx,
    )

    out = _call("inspect_timeline", {"time_sec": 0.25, "label": "paint-smoke"}, ctx)
    frame = ctx.registry.get(out["frame_asset_ids"][0]).path
    px = _pixel(frame, 256, 256)
    assert px[1] > 180
    assert px[0] < 80 and px[2] < 80
