"""animate_captions: per-word animated captions wired through ToolContext.

Renders per-frame (PIL), so the fixtures stay tiny. Asserts the tool produces a
new video asset from either plain text or explicit word timings, validates its
inputs, and rejects non-video assets.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


def _make_video(path: Path, *, duration: float = 1.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size=192x108:rate=12",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="v3-animcap",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def test_animate_captions_is_real_not_stub():
    assert "animate_captions" in DISPATCHER
    assert "stub" not in DISPATCHER["animate_captions"].__qualname__.lower()


def test_text_produces_new_animated_video(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    out = _call("animate_captions", {"asset_id": src, "text": "make it pop now"}, ctx)
    new = out["asset_id"]
    assert new != src
    rec = ctx.registry.get(new)
    assert rec.kind == "video" and rec.path.exists() and rec.path.stat().st_size > 0
    assert out["metadata"]["word_count"] == 4
    assert out["metadata"]["preset"] == "karaoke_pop" and out["metadata"]["timed"] is False


def test_explicit_word_timings_used(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    timings = [
        {"word": "hello", "start_seconds": 0.0, "end_seconds": 0.5},
        {"word": "world", "start_seconds": 0.5, "end_seconds": 1.0},
    ]
    out = _call("animate_captions", {"asset_id": src, "word_timings": timings, "preset": "quiet_captions"}, ctx)
    assert out["metadata"]["word_count"] == 2 and out["metadata"]["timed"] is True
    assert out["metadata"]["preset"] == "quiet_captions"


def test_requires_text_or_timings(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    with pytest.raises(ValueError):
        _call("animate_captions", {"asset_id": src}, ctx)


def test_bad_preset_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    with pytest.raises(ValueError):
        _call("animate_captions", {"asset_id": src, "text": "x", "preset": "nope"}, ctx)


def test_non_video_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    audio = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=1", str(audio)],
                   check=True, capture_output=True)
    aid = ctx.registry.add_external(audio, summary="a").asset_id
    with pytest.raises(ValueError):
        _call("animate_captions", {"asset_id": aid, "text": "x"}, ctx)
