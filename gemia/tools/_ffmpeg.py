"""Shared ffmpeg / ffprobe helpers for v3 tool dispatchers.

``run_ffmpeg_with_progress`` runs ffmpeg via ``subprocess.Popen`` with
``-progress pipe:1`` so we get a clean key=value stream on stdout we can
parse without fighting the carriage-return live-updating stderr line.

Progress callbacks are real: each ``out_time_us=`` line emits one
``ProgressUpdate`` with a percent derived from ``total_seconds``. If
``total_seconds`` is unknown (0 or None), percent stays None and the
frontend renders an indeterminate spinner. Honest reporting.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from gemia.tools._context import ProgressCallback, ProgressUpdate


def ffprobe_metadata(path: Path) -> dict[str, Any]:
    """Return parsed ffprobe JSON for the given media file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds (0.0 if unknown)."""
    meta = ffprobe_metadata(path)
    fmt = meta.get("format") or {}
    raw = fmt.get("duration")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def video_stream(metadata: dict[str, Any]) -> dict[str, Any] | None:
    for stream in metadata.get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream
    return None


def audio_stream(metadata: dict[str, Any]) -> dict[str, Any] | None:
    for stream in metadata.get("streams") or []:
        if stream.get("codec_type") == "audio":
            return stream
    return None


def short_summary(metadata: dict[str, Any]) -> str:
    """Compact human-readable description of a media file."""
    fmt = metadata.get("format") or {}
    parts: list[str] = []
    try:
        parts.append(f"{float(fmt.get('duration', 0)):.2f}s")
    except (TypeError, ValueError):
        pass
    vs = video_stream(metadata)
    if vs:
        w = vs.get("width")
        h = vs.get("height")
        if w and h:
            parts.append(f"{w}x{h}")
        fps_raw = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/0"
        try:
            num, den = fps_raw.split("/")
            if int(den) != 0:
                parts.append(f"{float(num) / float(den):.1f}fps")
        except (ValueError, ZeroDivisionError):
            pass
        codec = vs.get("codec_name")
        if codec:
            parts.append(str(codec))
    bitrate = fmt.get("bit_rate")
    if bitrate:
        try:
            parts.append(f"{int(bitrate) / 1000:.0f}kbps")
        except (TypeError, ValueError):
            pass
    return " ".join(parts) or "media"


async def run_ffmpeg_with_progress(
    cmd: list[str],
    *,
    total_seconds: float,
    progress: ProgressCallback,
) -> None:
    """Run ``ffmpeg`` and forward real progress to ``progress``.

    ``cmd`` must start with ``ffmpeg`` and end with the output path.
    The helper appends ``-loglevel error`` and ``-progress pipe:1``.
    Raises RuntimeError on non-zero exit with stderr tail in the message.
    """
    full = list(cmd)
    if full[0] != "ffmpeg":
        raise ValueError("cmd[0] must be 'ffmpeg'")
    if "-loglevel" not in full:
        full[1:1] = ["-loglevel", "error"]
    if "-progress" not in full:
        full[-1:-1] = ["-progress", "pipe:1"]

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.Popen(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        ),
    )

    stderr_buf: list[str] = []

    async def drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await loop.run_in_executor(None, proc.stderr.readline)
            if not line:
                return
            stderr_buf.append(line)

    stderr_task = asyncio.create_task(drain_stderr())

    try:
        assert proc.stdout is not None
        while True:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except (ValueError, IndexError):
                    continue
                secs = us / 1_000_000.0
                if total_seconds and total_seconds > 0:
                    pct = max(0.0, min(100.0, 100.0 * secs / total_seconds))
                    progress(ProgressUpdate(percent=pct, message=f"{secs:.1f}s / {total_seconds:.1f}s"))
                else:
                    progress(ProgressUpdate(percent=None, message=f"{secs:.1f}s processed"))
            elif line == "progress=end":
                if total_seconds and total_seconds > 0:
                    progress(ProgressUpdate(percent=100.0, message="finalizing"))
                break
    except asyncio.CancelledError:
        proc.kill()
        raise
    finally:
        if proc.poll() is None and asyncio.current_task() is not None and asyncio.current_task().cancelled():
            proc.kill()

    rc = await loop.run_in_executor(None, proc.wait)
    await stderr_task
    if rc != 0:
        tail = "".join(stderr_buf)[-1200:]
        raise RuntimeError(f"ffmpeg failed (exit {rc}): {tail}")


__all__ = [
    "ffprobe_metadata",
    "ffprobe_duration",
    "video_stream",
    "audio_stream",
    "short_summary",
    "run_ffmpeg_with_progress",
]
