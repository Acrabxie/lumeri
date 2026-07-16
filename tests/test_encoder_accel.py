"""GPU encoder detection, arg generation, and CPU double-try fallback.

Covers ``gemia.tools._ffmpeg``:
  * ``detect_supported_encoders`` parses ``ffmpeg -encoders`` and degrades
    gracefully when ffmpeg is missing.
  * ``get_video_encoder_args`` picks VideoToolbox / NVENC / QSV / libx264 by
    availability and preference order, honouring the ``GEMIA_VIDEO_ENCODER``
    override and quality presets.
  * ``downgrade_video_encoder`` rewrites a GPU command to its CPU equivalent.
  * ``run_ffmpeg_with_progress`` retries once on CPU when a GPU encode raises.

Detection/GPU paths are deterministic here: we monkeypatch
``detect_supported_encoders`` (or the subprocess it shells out to) rather than
depend on the host's hardware. The one real-hardware test is skipped unless a
GPU encoder is genuinely present.

Note: ``conftest`` pins ``GEMIA_VIDEO_ENCODER=cpu`` for the whole suite, so the
auto-detect tests re-enable it with ``monkeypatch.setenv(..., "auto")``.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from types import SimpleNamespace

import pytest

from gemia.tools import _ffmpeg


# --------------------------------------------------------------------------- #
# detect_supported_encoders
# --------------------------------------------------------------------------- #

_FFMPEG_ENCODERS_SAMPLE = """Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V....D libx265              libx265 H.265 / HEVC
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)
 V....D hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)
 A..... aac                  AAC (Advanced Audio Coding)
 A..... libopus              libopus Opus
"""


def _patch_encoders_probe(monkeypatch, stdout: str) -> None:
    def fake_run(cmd, *args, **kwargs):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
    monkeypatch.setattr(_ffmpeg.subprocess, "run", fake_run)
    _ffmpeg.detect_supported_encoders.cache_clear()


def test_detect_parses_video_encoders(monkeypatch):
    _patch_encoders_probe(monkeypatch, _FFMPEG_ENCODERS_SAMPLE)
    encoders = _ffmpeg.detect_supported_encoders()
    assert "libx264" in encoders
    assert "h264_videotoolbox" in encoders
    assert "hevc_videotoolbox" in encoders
    # audio encoders and the legend rows must not leak in
    assert "aac" not in encoders
    assert "libopus" not in encoders
    assert "Video" not in encoders
    _ffmpeg.detect_supported_encoders.cache_clear()


def test_detect_is_cached(monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, *args, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(stdout=_FFMPEG_ENCODERS_SAMPLE, stderr="", returncode=0)

    monkeypatch.setattr(_ffmpeg.subprocess, "run", fake_run)
    _ffmpeg.detect_supported_encoders.cache_clear()
    _ffmpeg.detect_supported_encoders()
    _ffmpeg.detect_supported_encoders()
    assert calls["n"] == 1  # second call served from lru_cache
    _ffmpeg.detect_supported_encoders.cache_clear()


def test_detect_missing_ffmpeg_returns_empty(monkeypatch, caplog):
    def boom(cmd, *args, **kwargs):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(_ffmpeg.subprocess, "run", boom)
    _ffmpeg.detect_supported_encoders.cache_clear()
    with caplog.at_level(logging.WARNING):
        assert _ffmpeg.detect_supported_encoders() == frozenset()
    assert any("CPU-only" in r.message for r in caplog.records)
    _ffmpeg.detect_supported_encoders.cache_clear()


# --------------------------------------------------------------------------- #
# get_video_encoder_args
# --------------------------------------------------------------------------- #

def _force_available(monkeypatch, encoders: set[str]) -> None:
    monkeypatch.setenv("GEMIA_VIDEO_ENCODER", "auto")
    monkeypatch.setattr(_ffmpeg, "detect_supported_encoders", lambda: frozenset(encoders))


def test_default_cpu_matches_historical_baseline(monkeypatch):
    # The whole point of the refactor: on a CPU host, default output is exactly
    # the string the tools used to hard-code.
    _force_available(monkeypatch, set())
    assert _ffmpeg.get_video_encoder_args("h264") == [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    ]


def test_videotoolbox_selected_on_mac(monkeypatch):
    _force_available(monkeypatch, {"h264_videotoolbox", "libx264"})
    assert _ffmpeg.get_video_encoder_args("h264") == [
        "-c:v", "h264_videotoolbox", "-q:v", "65",
    ]


def test_nvenc_selected(monkeypatch):
    _force_available(monkeypatch, {"h264_nvenc", "libx264"})
    assert _ffmpeg.get_video_encoder_args("h264") == [
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
    ]


def test_qsv_selected(monkeypatch):
    _force_available(monkeypatch, {"h264_qsv", "libx264"})
    assert _ffmpeg.get_video_encoder_args("h264") == [
        "-c:v", "h264_qsv", "-global_quality", "20",
    ]


def test_preference_order_prefers_videotoolbox(monkeypatch):
    # All three present: VideoToolbox wins (first in _GPU_FAMILIES).
    _force_available(monkeypatch, {"h264_videotoolbox", "h264_nvenc", "h264_qsv"})
    assert _ffmpeg.get_video_encoder_args("h264")[1] == "h264_videotoolbox"


def test_no_gpu_falls_back_to_cpu(monkeypatch):
    _force_available(monkeypatch, {"libx264", "aac"})
    assert _ffmpeg.get_video_encoder_args("h264")[1] == "libx264"


def test_h265_uses_hevc_encoders(monkeypatch):
    _force_available(monkeypatch, {"hevc_videotoolbox"})
    assert _ffmpeg.get_video_encoder_args("h265") == [
        "-c:v", "hevc_videotoolbox", "-q:v", "65",
    ]
    # alias
    _force_available(monkeypatch, set())
    assert _ffmpeg.get_video_encoder_args("hevc") == [
        "-c:v", "libx265", "-preset", "veryfast", "-crf", "24",
    ]


def test_quality_presets_differ(monkeypatch):
    _force_available(monkeypatch, set())
    default = _ffmpeg.get_video_encoder_args("h264", "default")
    high = _ffmpeg.get_video_encoder_args("h264", "high")
    fast = _ffmpeg.get_video_encoder_args("h264", "fast")
    assert default[-1] == "20" and high[-1] == "18" and fast[-1] == "23"
    assert high[3] == "slow" and fast[3] == "ultrafast"


def test_env_override_forces_cpu_even_with_gpu(monkeypatch):
    monkeypatch.setenv("GEMIA_VIDEO_ENCODER", "cpu")
    monkeypatch.setattr(_ffmpeg, "detect_supported_encoders",
                        lambda: frozenset({"h264_videotoolbox"}))
    assert _ffmpeg.get_video_encoder_args("h264")[1] == "libx264"


def test_cpu_video_encoder_args_always_cpu(monkeypatch):
    _force_available(monkeypatch, {"h264_videotoolbox", "h264_nvenc"})
    assert _ffmpeg.cpu_video_encoder_args("h264") == [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    ]
    assert _ffmpeg.cpu_video_encoder_args("h265")[1] == "libx265"


# --------------------------------------------------------------------------- #
# downgrade_video_encoder
# --------------------------------------------------------------------------- #

def test_downgrade_videotoolbox_to_cpu():
    cmd = ["ffmpeg", "-y", "-i", "in.mp4",
           "-c:v", "h264_videotoolbox", "-q:v", "65",
           "-c:a", "aac", "out.mp4"]
    new, changed = _ffmpeg.downgrade_video_encoder(cmd)
    assert changed is True
    assert new == ["ffmpeg", "-y", "-i", "in.mp4",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", "out.mp4"]
    assert "-q:v" not in new and "h264_videotoolbox" not in new


def test_downgrade_nvenc_strips_preset_and_cq():
    cmd = ["ffmpeg", "-i", "in.mp4",
           "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "out.mp4"]
    new, changed = _ffmpeg.downgrade_video_encoder(cmd)
    assert changed is True
    assert new == ["ffmpeg", "-i", "in.mp4",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "out.mp4"]
    assert "p4" not in new and "-cq" not in new


def test_downgrade_hevc_to_libx265():
    cmd = ["ffmpeg", "-i", "in.mp4", "-c:v", "hevc_videotoolbox", "-q:v", "65", "out.mp4"]
    new, changed = _ffmpeg.downgrade_video_encoder(cmd)
    assert changed is True
    assert "-c:v" in new and new[new.index("-c:v") + 1] == "libx265"
    assert "-crf" in new and new[new.index("-crf") + 1] == "24"


def test_downgrade_bitrate_mode_keeps_bitrate_no_crf():
    cmd = ["ffmpeg", "-i", "in.mp4",
           "-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "8M", "-maxrate", "8M", "out.mp4"]
    new, changed = _ffmpeg.downgrade_video_encoder(cmd)
    assert changed is True
    assert "-b:v" in new and new[new.index("-b:v") + 1] == "8M"
    assert "-maxrate" in new  # non-encoder flags preserved
    assert "-crf" not in new  # would conflict with -b:v
    assert new[new.index("-c:v") + 1] == "libx264"


def test_downgrade_cpu_cmd_is_noop():
    cmd = ["ffmpeg", "-i", "in.mp4",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "out.mp4"]
    new, changed = _ffmpeg.downgrade_video_encoder(cmd)
    assert changed is False
    assert new == cmd


# --------------------------------------------------------------------------- #
# run_ffmpeg_with_progress double-try
# --------------------------------------------------------------------------- #

def test_double_try_falls_back_to_cpu(monkeypatch, caplog):
    calls: list[list[str]] = []

    async def fake_once(cmd, *, total_seconds, progress):
        calls.append(list(cmd))
        if "h264_videotoolbox" in cmd:
            raise RuntimeError("ffmpeg failed (exit 1): videotoolbox rejected frame")

    monkeypatch.setattr(_ffmpeg, "_run_ffmpeg_once", fake_once)
    cmd = ["ffmpeg", "-y", "-i", "in.mp4",
           "-c:v", "h264_videotoolbox", "-q:v", "65", "out.mp4"]
    with caplog.at_level(logging.WARNING):
        asyncio.run(_ffmpeg.run_ffmpeg_with_progress(
            cmd, total_seconds=1.0, progress=lambda _u: None))

    assert len(calls) == 2, "should try GPU once, then CPU once"
    assert "h264_videotoolbox" in calls[0]
    assert "libx264" in calls[1] and "h264_videotoolbox" not in calls[1]
    assert "-q:v" not in calls[1]
    assert any("falling back to CPU" in r.message for r in caplog.records)


def test_cpu_failure_does_not_retry(monkeypatch):
    calls: list[list[str]] = []

    async def fake_once(cmd, *, total_seconds, progress):
        calls.append(list(cmd))
        raise RuntimeError("ffmpeg failed (exit 1): bad input")

    monkeypatch.setattr(_ffmpeg, "_run_ffmpeg_once", fake_once)
    cmd = ["ffmpeg", "-i", "in.mp4",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "out.mp4"]
    with pytest.raises(RuntimeError, match="bad input"):
        asyncio.run(_ffmpeg.run_ffmpeg_with_progress(
            cmd, total_seconds=1.0, progress=lambda _u: None))
    assert len(calls) == 1, "CPU-only command must not be retried"


def test_gpu_failure_then_cpu_failure_raises_cpu_error(monkeypatch):
    async def fake_once(cmd, *, total_seconds, progress):
        if "h264_nvenc" in cmd:
            raise RuntimeError("gpu error")
        raise RuntimeError("cpu error too")

    monkeypatch.setattr(_ffmpeg, "_run_ffmpeg_once", fake_once)
    cmd = ["ffmpeg", "-i", "in.mp4", "-c:v", "h264_nvenc", "-cq", "20", "out.mp4"]
    with pytest.raises(RuntimeError, match="cpu error too"):
        asyncio.run(_ffmpeg.run_ffmpeg_with_progress(
            cmd, total_seconds=1.0, progress=lambda _u: None))


def test_success_first_try_no_fallback(monkeypatch):
    calls: list[list[str]] = []

    async def fake_once(cmd, *, total_seconds, progress):
        calls.append(list(cmd))

    monkeypatch.setattr(_ffmpeg, "_run_ffmpeg_once", fake_once)
    cmd = ["ffmpeg", "-i", "in.mp4", "-c:v", "h264_videotoolbox", "-q:v", "65", "out.mp4"]
    asyncio.run(_ffmpeg.run_ffmpeg_with_progress(
        cmd, total_seconds=1.0, progress=lambda _u: None))
    assert len(calls) == 1  # succeeded on the GPU try, no retry


# --------------------------------------------------------------------------- #
# real hardware smoke test (skipped when no GPU encoder is present)
# --------------------------------------------------------------------------- #

def _real_gpu_encoder() -> str | None:
    _ffmpeg.detect_supported_encoders.cache_clear()
    available = _ffmpeg.detect_supported_encoders()
    _ffmpeg.detect_supported_encoders.cache_clear()
    for family in _ffmpeg._GPU_FAMILIES:
        enc = _ffmpeg._GPU_ENCODER[("h264", family)]
        if enc in available:
            return enc
    return None


@pytest.mark.skipif(_real_gpu_encoder() is None, reason="no hardware H.264 encoder on this host")
def test_real_gpu_encode_produces_h264(tmp_path, monkeypatch):
    """On a host with a real GPU encoder, an auto-selected encode must produce a
    playable H.264 file — proving the emitted args are actually accepted."""
    monkeypatch.setenv("GEMIA_VIDEO_ENCODER", "auto")
    _ffmpeg.detect_supported_encoders.cache_clear()
    encoder_args = _ffmpeg.get_video_encoder_args("h264")
    assert encoder_args[1] != "libx264", "expected a hardware encoder on this host"

    out = tmp_path / "gpu.mp4"
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           "-i", "testsrc2=size=320x240:rate=30:duration=1",
           *encoder_args, "-pix_fmt", "yuv420p", str(out)]

    async def _go():
        await _ffmpeg.run_ffmpeg_with_progress(cmd, total_seconds=1.0, progress=lambda _u: None)

    asyncio.run(_go())
    assert out.exists() and out.stat().st_size > 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "h264"
    _ffmpeg.detect_supported_encoders.cache_clear()
