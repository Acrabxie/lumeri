"""Smoke tests for v3 batch-1 verbs (composite / arrange_timeline / mix_audio
/ transform_geometry / edit_image / extract_frame).

Follows the existing pattern from ``test_v3_infra_regressions.py``: stub
``run_ffmpeg_with_progress`` and ``ffprobe_*`` helpers, then assert on
ffmpeg cmd composition + registry shape. Real ffmpeg is never invoked.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gemia.errors import ToolError
from gemia.tools import (
    arrange_timeline as arrange_timeline_tool,
    composite as composite_tool,
    edit_image as edit_image_tool,
    extract_frame as extract_frame_tool,
    mix_audio as mix_audio_tool,
    transform_geometry as transform_geometry_tool,
)
from gemia.tools._context import AssetRegistry, ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test-batch1",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _make_video(ctx: ToolContext, tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(b"video")
    return ctx.registry.add_external(path).asset_id


def _make_image(ctx: ToolContext, tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.png"
    path.write_bytes(b"image")
    return ctx.registry.add_external(path).asset_id


def _make_audio(ctx: ToolContext, tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.wav"
    path.write_bytes(b"audio")
    return ctx.registry.add_external(path).asset_id


# ───────────────────────────── composite ─────────────────────────────


def test_composite_alpha_overlay_includes_overlay_input(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    base = _make_video(ctx, tmp_path, "base")
    overlay = _make_image(ctx, tmp_path, "logo")
    seen = {}

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(composite_tool, "ffprobe_duration", lambda _p: 3.0)
    monkeypatch.setattr(composite_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        composite_tool.dispatch(
            {
                "base_asset_id": base,
                "overlay_asset_id": overlay,
                "mode": "alpha",
                "opacity": 0.6,
                "position": {"x": 40, "y": 80},
            },
            ctx,
        )
    )

    cmd_text = " ".join(seen["cmd"])
    assert "[0:v][ovl]overlay=40:80[out]" in cmd_text
    assert "colorchannelmixer=aa=0.6000" in cmd_text
    record = ctx.registry.get(result["asset_id"])
    assert record.kind == "video"
    assert result["metadata"]["mode"] == "alpha"


def test_composite_blend_modes_use_blend_filter(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    base = _make_image(ctx, tmp_path, "base")
    overlay = _make_image(ctx, tmp_path, "ov")
    seen = {}

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(composite_tool, "ffprobe_duration", lambda _p: 0.0)
    monkeypatch.setattr(composite_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        composite_tool.dispatch(
            {"base_asset_id": base, "overlay_asset_id": overlay, "mode": "multiply"},
            ctx,
        )
    )
    assert "blend=all_mode='multiply'" in " ".join(seen["cmd"])
    assert ctx.registry.get(result["asset_id"]).kind == "image"


def test_composite_rejects_bad_opacity(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    base = _make_image(ctx, tmp_path, "b")
    overlay = _make_image(ctx, tmp_path, "o")
    with pytest.raises(ValueError, match="opacity"):
        asyncio.run(
            composite_tool.dispatch(
                {"base_asset_id": base, "overlay_asset_id": overlay, "mode": "alpha", "opacity": 1.5},
                ctx,
            )
        )


# ─────────────────────────── arrange_timeline ───────────────────────────


def test_arrange_timeline_all_cut_uses_concat_demuxer(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_video(ctx, tmp_path, "a")
    b = _make_video(ctx, tmp_path, "b")
    seen = {}

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        seen["cmd"] = cmd
        seen["total"] = total_seconds
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(arrange_timeline_tool, "ffprobe_duration", lambda _p: 2.0)
    monkeypatch.setattr(
        arrange_timeline_tool, "ffprobe_metadata",
        lambda _p: {"streams": [{"codec_type": "video"}]},
    )
    monkeypatch.setattr(arrange_timeline_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        arrange_timeline_tool.dispatch({"asset_ids": [a, b]}, ctx)
    )

    cmd = " ".join(seen["cmd"])
    assert "-f concat" in cmd
    assert seen["total"] == pytest.approx(4.0)
    assert result["metadata"]["all_cut"] is True
    assert result["metadata"]["clip_count"] == 2


def test_arrange_timeline_dissolve_uses_xfade_with_offset(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_video(ctx, tmp_path, "a")
    b = _make_video(ctx, tmp_path, "b")
    seen = {}

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        seen["cmd"] = cmd
        seen["total"] = total_seconds
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(arrange_timeline_tool, "ffprobe_duration", lambda _p: 4.0)
    monkeypatch.setattr(
        arrange_timeline_tool, "ffprobe_metadata",
        lambda _p: {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]},
    )
    monkeypatch.setattr(arrange_timeline_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        arrange_timeline_tool.dispatch(
            {
                "asset_ids": [a, b],
                "transitions": [{"between_index": 0, "kind": "dissolve", "duration_sec": 0.5}],
            },
            ctx,
        )
    )

    cmd = " ".join(seen["cmd"])
    assert "xfade=transition=fade" in cmd
    assert "duration=0.500" in cmd
    assert "offset=3.500" in cmd
    # 4 + 4 - 0.5 = 7.5
    assert seen["total"] == pytest.approx(7.5)
    assert "acrossfade=d=0.500" in cmd
    assert result["metadata"]["all_cut"] is False


def test_arrange_timeline_rejects_out_of_range_transition(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_video(ctx, tmp_path, "a")
    b = _make_video(ctx, tmp_path, "b")
    monkeypatch.setattr(arrange_timeline_tool, "ffprobe_duration", lambda _p: 2.0)
    monkeypatch.setattr(
        arrange_timeline_tool, "ffprobe_metadata",
        lambda _p: {"streams": [{"codec_type": "video"}]},
    )
    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(
            arrange_timeline_tool.dispatch(
                {
                    "asset_ids": [a, b],
                    "transitions": [{"between_index": 5, "kind": "dissolve", "duration_sec": 0.5}],
                },
                ctx,
            )
        )


# ───────────────────────────── mix_audio ─────────────────────────────


def test_mix_audio_concat_with_levels_applies_volume_filters(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_audio(ctx, tmp_path, "a")
    b = _make_audio(ctx, tmp_path, "b")
    seen = {}

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        seen["cmd"] = cmd
        seen["total"] = total_seconds
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(mix_audio_tool, "ffprobe_duration", lambda _p: 5.0)
    monkeypatch.setattr(mix_audio_tool, "run_ffmpeg_with_progress", fake_run)

    asyncio.run(
        mix_audio_tool.dispatch(
            {"asset_ids": [a, b], "mode": "concat", "levels_db": [0.0, -6.0]},
            ctx,
        )
    )
    cmd = " ".join(seen["cmd"])
    assert "volume=0.501187" in cmd  # 10^(-6/20)
    assert "concat=n=2:v=0:a=1" in cmd
    assert seen["total"] == pytest.approx(10.0)


def test_mix_audio_duck_chains_sidechain(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    bed = _make_audio(ctx, tmp_path, "bed")
    vox = _make_audio(ctx, tmp_path, "vox")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(mix_audio_tool, "ffprobe_duration", lambda _p: 3.0)
    monkeypatch.setattr(mix_audio_tool, "run_ffmpeg_with_progress", fake_run)

    asyncio.run(
        mix_audio_tool.dispatch({"asset_ids": [bed, vox], "mode": "duck"}, ctx)
    )
    cmd = " ".join(fake_run.cmd)  # type: ignore[attr-defined]
    assert "sidechaincompress" in cmd
    assert "amix=inputs=2:duration=longest" in cmd


def test_mix_audio_rejects_single_input(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_audio(ctx, tmp_path, "a")
    with pytest.raises(ValueError, match="at least 2"):
        asyncio.run(mix_audio_tool.dispatch({"asset_ids": [a], "mode": "mix"}, ctx))


def test_mix_audio_rejects_levels_length_mismatch(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = _make_audio(ctx, tmp_path, "a")
    b = _make_audio(ctx, tmp_path, "b")
    with pytest.raises(ValueError, match="length"):
        asyncio.run(
            mix_audio_tool.dispatch(
                {"asset_ids": [a, b], "mode": "mix", "levels_db": [0.0]}, ctx
            )
        )


# ─────────────────────────── transform_geometry ───────────────────────────


def test_transform_geometry_crop_builds_crop_filter(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_video(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(transform_geometry_tool, "ffprobe_duration", lambda _p: 2.0)
    monkeypatch.setattr(transform_geometry_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        transform_geometry_tool.dispatch(
            {
                "asset_id": asset,
                "operation": "crop",
                "params": {"x": 10, "y": 20, "w": 640, "h": 360},
            },
            ctx,
        )
    )
    assert "-vf crop=640:360:10:20" in " ".join(fake_run.cmd)  # type: ignore[attr-defined]
    assert result["metadata"]["params"] == {"x": 10, "y": 20, "w": 640, "h": 360}


def test_transform_geometry_scale_with_factor(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(transform_geometry_tool, "ffprobe_duration", lambda _p: 0.0)
    monkeypatch.setattr(transform_geometry_tool, "run_ffmpeg_with_progress", fake_run)

    asyncio.run(
        transform_geometry_tool.dispatch(
            {"asset_id": asset, "operation": "scale", "params": {"factor": 2.0}}, ctx
        )
    )
    assert "scale=iw*2.0:ih*2.0" in " ".join(fake_run.cmd)  # type: ignore[attr-defined]


def test_transform_geometry_perspective_requires_all_four_corners(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")
    with pytest.raises(ValueError, match="top_right"):
        asyncio.run(
            transform_geometry_tool.dispatch(
                {
                    "asset_id": asset,
                    "operation": "perspective",
                    "params": {
                        "top_left": [0, 0],
                        # top_right intentionally missing
                        "bottom_left": [0, 100],
                        "bottom_right": [100, 100],
                    },
                },
                ctx,
            )
        )


def test_transform_geometry_scale_rejects_factor_with_dim(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")
    with pytest.raises(ValueError, match="factor OR w/h"):
        asyncio.run(
            transform_geometry_tool.dispatch(
                {"asset_id": asset, "operation": "scale", "params": {"factor": 2.0, "w": 100}},
                ctx,
            )
        )


# ───────────────────────────── edit_image ─────────────────────────────


def test_edit_image_blur_builds_gblur_filter(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(edit_image_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        edit_image_tool.dispatch(
            {"asset_id": asset, "operation": "blur", "params": {"radius": 6.0}}, ctx
        )
    )
    assert "gblur=sigma=3.000" in " ".join(fake_run.cmd)  # type: ignore[attr-defined]
    assert ctx.registry.get(result["asset_id"]).kind == "image"


def test_edit_image_denoise_uses_nlmeans(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(edit_image_tool, "run_ffmpeg_with_progress", fake_run)

    asyncio.run(
        edit_image_tool.dispatch(
            {"asset_id": asset, "operation": "denoise", "params": {"strength": 2.5}}, ctx
        )
    )
    assert "nlmeans=s=2.50" in " ".join(fake_run.cmd)  # type: ignore[attr-defined]


def test_edit_image_remove_background_raises_not_implemented(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_image(ctx, tmp_path, "src")
    # Honest typed failure (recovery=switch_tool) rather than a fake cut-out.
    with pytest.raises(ToolError, match="ML model") as exc_info:
        asyncio.run(
            edit_image_tool.dispatch(
                {"asset_id": asset, "operation": "remove_background", "params": {}}, ctx
            )
        )
    assert exc_info.value.code == "E_NOT_IMPLEMENTED"
    assert exc_info.value.recovery == "switch_tool"


def test_edit_image_rejects_video_input(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_video(ctx, tmp_path, "src")
    with pytest.raises(ToolError, match="image asset") as exc_info:
        asyncio.run(
            edit_image_tool.dispatch(
                {"asset_id": asset, "operation": "blur", "params": {"radius": 3.0}}, ctx
            )
        )
    assert exc_info.value.code == "E_UNSUPPORTED"


# ───────────────────────────── extract_frame ─────────────────────────────


def test_extract_frame_clamps_past_end(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_video(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")
        fake_run.cmd = cmd  # type: ignore[attr-defined]

    monkeypatch.setattr(extract_frame_tool, "ffprobe_duration", lambda _p: 2.0)
    monkeypatch.setattr(extract_frame_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        extract_frame_tool.dispatch({"asset_id": asset, "time_sec": 99.0}, ctx)
    )
    assert result["metadata"]["clamped"] is True
    assert result["metadata"]["time_sec"] == pytest.approx(1.95, abs=1e-3)
    cmd = " ".join(fake_run.cmd)  # type: ignore[attr-defined]
    assert "-ss 1.950" in cmd


def test_extract_frame_within_duration_not_clamped(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_video(ctx, tmp_path, "src")

    async def fake_run(cmd, *, total_seconds, progress) -> None:
        Path(cmd[-1]).write_bytes(b"out")

    monkeypatch.setattr(extract_frame_tool, "ffprobe_duration", lambda _p: 10.0)
    monkeypatch.setattr(extract_frame_tool, "run_ffmpeg_with_progress", fake_run)

    result = asyncio.run(
        extract_frame_tool.dispatch({"asset_id": asset, "time_sec": 4.5}, ctx)
    )
    assert result["metadata"]["clamped"] is False
    assert result["metadata"]["time_sec"] == pytest.approx(4.5)
    record = ctx.registry.get(result["asset_id"])
    assert record.kind == "image"
    assert record.path.suffix == ".png"


def test_extract_frame_rejects_negative_time(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    asset = _make_video(ctx, tmp_path, "src")
    with pytest.raises(ValueError, match=">= 0"):
        asyncio.run(
            extract_frame_tool.dispatch({"asset_id": asset, "time_sec": -1.0}, ctx)
        )


# ─────────────────────────── DISPATCHER wiring ───────────────────────────


def test_dispatcher_registers_all_six_batch1_verbs() -> None:
    from gemia.tools import DISPATCHER

    for name in (
        "composite",
        "arrange_timeline",
        "mix_audio",
        "transform_geometry",
        "edit_image",
        "extract_frame",
    ):
        assert name in DISPATCHER
        # The dispatcher must not be a stub anymore — stubs are named "stub_*".
        assert not DISPATCHER[name].__name__.startswith("stub_"), (
            f"{name} dispatcher is still a stub"
        )


def test_provider_media_dispatchers_are_real() -> None:
    from gemia.tools import DISPATCHER

    # generate_image, generate_video, and generate_audio all use Vertex now.
    # search_library moved to a real cheap lookup in v4 M3.
    for name in ("generate_image", "generate_video", "generate_audio", "search_library"):
        assert not DISPATCHER[name].__name__.startswith("stub_"), (
            f"{name} dispatcher should not be a stub"
        )
