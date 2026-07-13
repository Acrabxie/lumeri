"""DAY3: export codec / color / fps / bitrate options, verified via ffprobe."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from gemia.errors import ToolError
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(session_id="export", output_dir=tmp_path,
                       registry=AssetRegistry(), emit_progress=lambda _u: None)


def _run(name: str, args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _make_src(tmp_path: Path, *, dur: float = 2.0) -> Path:
    """A small real video (testsrc2 + sine tone) written to disk."""
    out = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x480:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        capture_output=True, check=True,
    )
    return out


def _probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,color_primaries,color_transfer,color_space,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)["streams"][0]


def _reg(ctx: ToolContext, path: Path) -> str:
    return ctx.registry.add_external(path).asset_id


def test_export_default_is_h264(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft"}, ctx)
    path = ctx.registry.get(out["asset_id"]).path
    assert _probe(path)["codec_name"] == "h264"
    assert out["metadata"]["codec"] == "h264"


def test_export_h265(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft", "codec": "h265"}, ctx)
    path = ctx.registry.get(out["asset_id"]).path
    assert _probe(path)["codec_name"] == "hevc"
    assert out["metadata"]["codec"] == "h265"


def test_export_hevc_alias(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mov", "quality": "draft", "codec": "hevc"}, ctx)
    assert _probe(ctx.registry.get(out["asset_id"]).path)["codec_name"] == "hevc"


def test_export_bt709_color_tags(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft", "color": "bt709"}, ctx)
    st = _probe(ctx.registry.get(out["asset_id"]).path)
    assert st["color_primaries"] == "bt709"
    assert st["color_transfer"] == "bt709"
    assert st["color_space"] == "bt709"


def test_export_fps_override(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft", "fps": 24}, ctx)
    assert _probe(ctx.registry.get(out["asset_id"]).path)["r_frame_rate"] == "24/1"


def test_export_target_bitrate(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "mp4", "quality": "720p",
                          "video_bitrate": "300k"}, ctx)
    path = ctx.registry.get(out["asset_id"]).path
    assert path.exists() and path.stat().st_size > 0
    assert _probe(path)["codec_name"] == "h264"
    assert out["metadata"]["video_bitrate"] == "300k"


def test_export_webm_still_vp9(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    out = _run("export", {"asset_id": sid, "format": "webm", "quality": "draft"}, ctx)
    assert _probe(ctx.registry.get(out["asset_id"]).path)["codec_name"] == "vp9"


def test_export_rejects_bad_codec(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    with pytest.raises(ToolError):
        _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft", "codec": "av1x"}, ctx)


def test_export_rejects_bad_bitrate(tmp_path):
    ctx = _ctx(tmp_path)
    sid = _reg(ctx, _make_src(tmp_path))
    with pytest.raises(ToolError):
        _run("export", {"asset_id": sid, "format": "mp4", "quality": "draft",
                        "video_bitrate": "lots"}, ctx)
