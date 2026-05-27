"""color_grade: apply a named look or free-form look description.

Named looks map to deterministic ffmpeg filter strings. Free-form looks
(any string not in the named list) fall through to a curves+eq mapping
that the model can describe later via analyze_media if it wants to check
the result.

intensity (0..1) blends the graded result with the original via a
split + blend filter graph. intensity=1.0 (default) applies the look
at full strength; intensity=0 yields the original unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import ffprobe_duration, run_ffmpeg_with_progress


_NAMED_LOOKS: dict[str, str] = {
    "warm": (
        "eq=saturation=1.10:gamma=1.02,"
        "curves=r='0/0 0.5/0.58 1/1':g='0/0 0.5/0.50 1/0.97':b='0/0 0.5/0.38 1/0.82'"
    ),
    "cool": (
        "eq=saturation=1.10:gamma=1.02,"
        "curves=r='0/0 0.5/0.42 1/0.95':g='0/0 0.5/0.50 1/0.98':b='0/0 0.5/0.62 1/1'"
    ),
    "vintage": (
        "eq=saturation=0.72:contrast=0.92:gamma=1.05,"
        "curves=preset=vintage"
    ),
    "cinematic": (
        "eq=saturation=0.90:contrast=1.08:gamma=0.98,"
        "curves=r='0/0.02 0.5/0.52 1/0.98':g='0/0 0.5/0.50 1/0.96':b='0/0.05 0.5/0.48 1/0.88'"
    ),
    "teal_orange": (
        "eq=saturation=1.20,"
        "curves=r='0/0 0.3/0.30 0.6/0.66 1/1':g='0/0 0.5/0.50 1/0.95':b='0/0.04 0.3/0.42 0.6/0.42 1/0.78'"
    ),
    "neutral": "null",
}


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    asset_id = str(args["asset_id"])
    look = str(args["look"]).strip()
    intensity_raw = args.get("intensity", 1.0)
    try:
        intensity = float(intensity_raw)
    except (TypeError, ValueError):
        raise ValueError(f"intensity must be a number 0..1, got {intensity_raw!r}")
    if not 0.0 <= intensity <= 1.0:
        raise ValueError(f"intensity must be in [0, 1], got {intensity}")

    src = ctx.registry.get(asset_id)
    if src.kind != "video":
        raise ValueError(
            f"color_grade in milestone 1 supports video assets only, got {src.kind!r}"
        )

    filter_str, look_label = _resolve_look(look)
    if filter_str == "null" or intensity == 0.0:
        filter_graph = "[0:v]null[out]"
        filter_label = "(no-op)"
    elif intensity >= 1.0:
        filter_graph = f"[0:v]{filter_str}[out]"
        filter_label = filter_str
    else:
        filter_graph = (
            f"[0:v]split=2[orig][src];"
            f"[src]{filter_str}[graded];"
            f"[orig][graded]blend=all_mode='normal':all_opacity={intensity:.3f}[out]"
        )
        filter_label = f"{filter_str} (blended at {intensity:.2f})"

    duration = ffprobe_duration(src.path)
    new_id = ctx.registry.allocate_id("video")
    out_path = ctx.child_path(new_id, ".mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src.path),
        "-filter_complex", filter_graph,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=duration, progress=ctx.emit_progress)
    summary = f"graded {asset_id} with look={look_label!r} intensity={intensity:.2f}"
    record = ctx.registry.register_output(
        new_id, kind="video", path=out_path, summary=summary, lineage=[asset_id]
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {
            "look": look_label,
            "intensity": intensity,
            "filter": filter_label,
            "duration_sec": duration,
        },
    }


def _resolve_look(look: str) -> tuple[str, str]:
    key = look.lower().strip().replace(" ", "_").replace("-", "_")
    if key in _NAMED_LOOKS:
        return _NAMED_LOOKS[key], key
    # Free-form look — apply the generic warm-shifted curve so the result
    # is visibly different from the source. The model can describe what it
    # actually wanted via analyze_media on the result.
    return _NAMED_LOOKS["warm"], f"freeform:{look}"


__all__ = ["dispatch"]
