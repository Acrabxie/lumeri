"""subtitle: caption a video (from-text always-works path + transcribe degrade).

Uses a synthetic testsrc video. The from-text path needs no ASR, so it is the
core assertion; the transcribe path is only checked for a *clean* failure when
Whisper is absent (it must guide the user, not crash cryptically).
"""
from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools.subtitle import _split_cues

_HAS_WHISPER = importlib.util.find_spec("whisper") is not None


def _make_video(path: Path, *, duration: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size=160x90:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=" + str(duration),
         "-pix_fmt", "yuv420p", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="v3-subtitle",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


# ── cue splitting (pure) ────────────────────────────────────────────────
def test_split_cues_covers_duration():
    cues = _split_cues("First sentence. Second sentence here. Third!", 9.0)
    assert cues[0]["start"] == 0.0
    assert abs(cues[-1]["end"] - 9.0) < 0.01
    for a, b in zip(cues, cues[1:]):
        assert b["start"] >= a["start"]
    assert all(c["end"] > c["start"] for c in cues)


def test_split_cues_wraps_cjk_without_spaces():
    cues = _split_cues("这是一段很长的中文旁白文字没有任何空格用来验证在中文场景下依然能够按照最大字符数正确换行分句显示。", 6.0)
    assert len(cues) >= 2


# ── from_text: the always-works path ────────────────────────────────────
def test_subtitle_is_real_not_stub():
    assert "subtitle" in DISPATCHER
    assert "stub" not in DISPATCHER["subtitle"].__qualname__.lower()


def test_from_text_burns_and_returns_new_asset(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    out = _call("subtitle", {"asset_id": src, "text": "Hello there. This is a captioned clip."}, ctx)
    new = out["asset_id"]
    assert new != src
    rec = ctx.registry.get(new)
    assert rec.kind == "video" and rec.path.exists() and rec.path.suffix == ".mp4"
    assert out["metadata"]["cue_count"] >= 2 and out["metadata"]["burned"] is True


def test_explicit_cues_are_used(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    cues = [{"start": 0.0, "end": 1.5, "text": "one"}, {"start": 1.5, "end": 3.0, "text": "two"}]
    out = _call("subtitle", {"asset_id": src, "cues": cues}, ctx)
    assert out["metadata"]["cue_count"] == 2


def test_soft_mux_produces_toggleable_track(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    out = _call("subtitle", {"asset_id": src, "text": "Soft track caption.", "burn": False}, ctx)
    rec = ctx.registry.get(out["asset_id"])
    assert rec.path.suffix == ".mp4" and rec.path.exists() and out["metadata"]["burned"] is False
    # the muxed file carries a selectable subtitle stream
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", str(rec.path)],
        capture_output=True, text=True,
    )
    assert "subtitle" in probe.stdout


def test_from_text_requires_text_or_cues(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    with pytest.raises(ValueError):
        _call("subtitle", {"asset_id": src}, ctx)


def test_non_video_asset_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    audio = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=1", str(audio)],
                   check=True, capture_output=True)
    aid = ctx.registry.add_external(audio, summary="a").asset_id
    with pytest.raises(ValueError):
        _call("subtitle", {"asset_id": aid, "text": "x"}, ctx)


@pytest.mark.skipif(_HAS_WHISPER, reason="whisper installed — degrade path not exercised")
def test_transcribe_without_whisper_degrades_cleanly(tmp_path):
    ctx = _ctx(tmp_path)
    src = ctx.registry.add_external(_make_video(tmp_path / "src.mp4"), summary="src").asset_id
    with pytest.raises(ValueError) as exc:
        _call("subtitle", {"asset_id": src, "source": "transcribe"}, ctx)
    assert "whisper" in str(exc.value).lower()  # actionable guidance, not a stack trace
