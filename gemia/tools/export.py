"""export: final encode at requested quality and format."""
from __future__ import annotations

from typing import Any

from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import ffprobe_duration, run_ffmpeg_with_progress


_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "4k":    {"height": 2160, "crf": "16", "preset": "slow"},
    "1080p": {"height": 1080, "crf": "18", "preset": "slow"},
    "720p":  {"height": 720,  "crf": "20", "preset": "medium"},
    "480p":  {"height": 480,  "crf": "23", "preset": "medium"},
    "draft": {"height": 480,  "crf": "28", "preset": "veryfast"},
}

_FORMAT_TUNING: dict[str, dict[str, list[str]]] = {
    "mp4": {
        "video": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "container": ["-movflags", "+faststart"],
    },
    "mov": {
        "video": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "container": [],
    },
    "webm": {
        "video": ["-c:v", "libvpx-vp9", "-b:v", "0"],
        "audio": ["-c:a", "libopus", "-b:a", "160k"],
        "container": [],
    },
    "gif": {
        "video": [],
        "audio": ["-an"],
        "container": [],
    },
}

_PLATFORM_TWEAKS: dict[str, list[str]] = {
    "youtube":   [],
    "instagram": ["-t", "60"],
    "tiktok":    ["-t", "180"],
    "twitter":   ["-t", "140", "-fs", "512M"],
    "prores":    [],
    "generic":   [],
}


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    asset_id = str(args["asset_id"])
    fmt = str(args["format"])
    quality = str(args["quality"])
    platform = str(args.get("platform") or "generic")

    src = ctx.registry.get(asset_id)
    if src.kind != "video":
        raise ValueError(f"export currently supports video assets only, got {src.kind!r}")

    if fmt not in _FORMAT_TUNING:
        raise ValueError(f"unknown format {fmt!r}. Known: {', '.join(_FORMAT_TUNING.keys())}")
    if quality not in _QUALITY_PROFILES:
        raise ValueError(
            f"unknown quality {quality!r}. Known: {', '.join(_QUALITY_PROFILES.keys())}"
        )
    if platform not in _PLATFORM_TWEAKS:
        raise ValueError(
            f"unknown platform {platform!r}. Known: {', '.join(_PLATFORM_TWEAKS.keys())}"
        )

    profile = _QUALITY_PROFILES[quality]
    tuning = _FORMAT_TUNING[fmt]
    duration = ffprobe_duration(src.path)
    output_kind = "image" if fmt == "gif" else "video"
    new_id = ctx.registry.allocate_id(output_kind)
    out_path = ctx.child_path(new_id, f".{fmt}")

    scale_filter = f"scale=-2:{profile['height']}"
    video_args: list[str] = ["-vf", scale_filter, *tuning["video"]]
    if fmt != "gif" and profile.get("crf"):
        video_args += ["-crf", str(profile["crf"]), "-preset", str(profile["preset"])]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src.path),
        *video_args,
        *tuning["audio"],
        *tuning["container"],
        *_PLATFORM_TWEAKS[platform],
        str(out_path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=duration, progress=ctx.emit_progress)

    summary = (
        f"exported {asset_id} as {fmt}/{quality} ({profile['height']}p, "
        f"platform={platform}) -> {out_path.name}"
    )
    record = ctx.registry.register_output(
        new_id, kind=output_kind, path=out_path, summary=summary, lineage=[asset_id]
    )
    return {
        "asset_id": new_id,
        "kind": record.kind,
        "summary": record.summary,
        "metadata": {
            "format": fmt,
            "quality": quality,
            "platform": platform,
            "height_target": profile["height"],
            "duration_sec": duration,
        },
    }


__all__ = ["dispatch"]
