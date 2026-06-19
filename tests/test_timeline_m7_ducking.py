"""M7 regression: track-level ducking + export-duration master contract.

Builds on M6 audio. Two concerns:
  * M7-A — export length is deterministic = project timeline.duration (the
    audio-inclusive master). A music bed longer than the last video clip plays
    over a black tail to the timeline end rather than over-running.
  * M7-B..D — a track may declare ``duck_under = <trigger>``; the renderer
    sidechain-compresses that bed's submix whenever the trigger is loud
    (mirrors gemia/tools/mix_audio.py duck mode).

Real ffmpeg, real ProjectStore, DISPATCHER verb layer.
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
from gemia.tools._ffmpeg import audio_stream, ffprobe_metadata, video_stream


# ── fixtures ─────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, session: str) -> ToolContext:
    registry = AssetRegistry()
    handle = ProjectHandle.open(tmp_path / "project", f"m7{session}", session_id=session)
    return ToolContext(
        session_id=session,
        output_dir=tmp_path,
        registry=registry,
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _silent_video(tmp_path: Path, name: str = "v.mp4", duration: float = 2.0) -> Path:
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration={duration}:size=128x128:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True,
    )
    return out


def _wav(tmp_path: Path, name: str, duration: float = 3.0, freq: int = 440) -> Path:
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={duration}", str(out)],
        capture_output=True, check=True,
    )
    return out


def _register(ctx: ToolContext, path: Path) -> str:
    return ctx.registry.add_external(path, summary="m7 test asset").asset_id


def _meta(export_path: str) -> dict[str, Any]:
    return ffprobe_metadata(Path(export_path))


# ── M7-A: export-duration master contract ──────────────────────────────────────


def test_export_audio_longer_than_video_pads_to_timeline(tmp_path: Path) -> None:
    """A music bed past the last video clip extends the export to timeline.duration
    (video padded black) with both a video and an audio stream."""
    ctx = _make_ctx(tmp_path, "durpad")
    _call("timeline_insert_clip", {"asset_id": _register(ctx, _silent_video(tmp_path, "v.mp4", 2.0))}, ctx)
    _call("timeline_insert_clip", {"asset_id": _register(ctx, _wav(tmp_path, "m.wav", 5.0))}, ctx)  # A1, 0-5s

    timeline_dur = ctx.project.load()["timeline"]["duration"]
    assert abs(timeline_dur - 5.0) < 1e-3

    out = _call("project_export", {"quality": "draft", "label": "durpad"}, ctx)
    meta = _meta(out["export_path"])
    assert video_stream(meta) is not None
    assert audio_stream(meta) is not None
    # Master length is timeline.duration; rendered MP4 is within codec/container margin.
    assert abs(out["duration"] - timeline_dur) < 0.3


def test_export_audio_shorter_than_video_unchanged(tmp_path: Path) -> None:
    """Audio shorter than the video leaves the video length as master (no padding)."""
    ctx = _make_ctx(tmp_path, "durshort")
    _call("timeline_insert_clip", {"asset_id": _register(ctx, _silent_video(tmp_path, "v.mp4", 4.0))}, ctx)
    _call("timeline_insert_clip", {"asset_id": _register(ctx, _wav(tmp_path, "m.wav", 2.0))}, ctx)  # A1, 0-2s

    timeline_dur = ctx.project.load()["timeline"]["duration"]
    assert abs(timeline_dur - 4.0) < 1e-3  # video end is the master

    out = _call("project_export", {"quality": "draft", "label": "durshort"}, ctx)
    meta = _meta(out["export_path"])
    assert audio_stream(meta) is not None
    assert abs(out["duration"] - 4.0) < 0.3
