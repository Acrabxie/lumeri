"""extract_frame: pull a single still frame from a video at a given timestamp.

Returns a new image asset_id. The timestamp is clamped to the video's
duration; if ``time_sec`` is beyond the end, we extract the last frame
and report the actual time used in the summary so the model knows.
"""
from __future__ import annotations

from typing import Any

from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import ffprobe_duration, run_ffmpeg_with_progress


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    asset_id = str(args["asset_id"])
    time_raw = args.get("time_sec")
    if time_raw is None:
        raise ValueError("extract_frame requires time_sec")
    try:
        time_sec = float(time_raw)
    except (TypeError, ValueError):
        raise ValueError(f"time_sec must be a number, got {time_raw!r}")
    if time_sec < 0:
        raise ValueError(f"time_sec must be >= 0, got {time_sec}")

    src = ctx.registry.get(asset_id)
    if src.kind != "video":
        raise ValueError(f"extract_frame requires a video asset, got {src.kind!r}")

    duration = ffprobe_duration(src.path)
    if duration > 0 and time_sec >= duration:
        # Use a small epsilon back from the end so we don't fall off the last frame.
        actual_time = max(0.0, duration - 0.05)
        clamped = True
    else:
        actual_time = time_sec
        clamped = False

    new_id = ctx.registry.allocate_id("image")
    out_path = ctx.child_path(new_id, ".png")
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{actual_time:.3f}",
        "-i", str(src.path),
        "-frames:v", "1",
        str(out_path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=0.0, progress=ctx.emit_progress)

    clamp_note = f" (clamped from {time_sec:.2f}s to {actual_time:.2f}s)" if clamped else ""
    summary = f"extracted frame from {asset_id} at {actual_time:.2f}s{clamp_note}"
    record = ctx.registry.register_output(
        new_id, kind="image", path=out_path, summary=summary, lineage=[asset_id]
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {
            "source_asset_id": asset_id,
            "time_sec": actual_time,
            "requested_time_sec": time_sec,
            "clamped": clamped,
            "source_duration_sec": duration,
        },
    }


__all__ = ["dispatch"]
