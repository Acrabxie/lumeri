"""mix_audio: combine multiple audio assets.

Modes:
- ``concat`` — play tracks end-to-end, in the order given.
- ``mix``    — overlay tracks (sum + normalize). Tracks of unequal length
  end when their source ends; the output runs as long as the longest.
- ``duck``   — first track is the bed (e.g. music). Subsequent tracks
  duck the bed via sidechain compression when they are loud (e.g. a
  voiceover ducks background music). Output length = longest track.

``levels_db`` is an optional per-track gain in dB. Same length as
``asset_ids``. Omit for unity gain on all tracks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import ffprobe_duration, run_ffmpeg_with_progress


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    asset_ids = list(args.get("asset_ids") or [])
    if len(asset_ids) < 2:
        raise ValueError("mix_audio requires at least 2 asset_ids")
    mode = str(args["mode"])
    if mode not in {"concat", "mix", "duck"}:
        raise ValueError(f"unknown mix mode: {mode!r}. Known: concat, mix, duck")

    levels = args.get("levels_db")
    if levels is not None:
        if not isinstance(levels, list):
            raise ValueError(f"levels_db must be a list, got {type(levels).__name__}")
        if len(levels) != len(asset_ids):
            raise ValueError(
                f"levels_db length {len(levels)} != asset_ids length {len(asset_ids)}"
            )
        try:
            gains = [float(v) for v in levels]
        except (TypeError, ValueError):
            raise ValueError(f"levels_db must be numbers in dB, got {levels!r}")
    else:
        gains = [0.0] * len(asset_ids)

    paths: list[Path] = []
    durations: list[float] = []
    for aid in asset_ids:
        rec = ctx.registry.get(aid)
        if rec.kind != "audio":
            raise ValueError(f"mix_audio input {aid} is {rec.kind!r}, expected audio")
        paths.append(rec.path)
        durations.append(ffprobe_duration(rec.path))

    new_id = ctx.registry.allocate_id("audio")
    out_path = ctx.child_path(new_id, ".wav")

    filter_lines: list[str] = []
    for i, gain in enumerate(gains):
        if gain != 0.0:
            mul = 10 ** (gain / 20.0)
            filter_lines.append(f"[{i}:a]volume={mul:.6f}[a{i}]")
        else:
            filter_lines.append(f"[{i}:a]anull[a{i}]")

    if mode == "concat":
        chain = "".join(f"[a{i}]" for i in range(len(asset_ids)))
        filter_lines.append(f"{chain}concat=n={len(asset_ids)}:v=0:a=1[out]")
        total = sum(durations)
    elif mode == "mix":
        chain = "".join(f"[a{i}]" for i in range(len(asset_ids)))
        filter_lines.append(
            f"{chain}amix=inputs={len(asset_ids)}:duration=longest:dropout_transition=0[out]"
        )
        total = max(durations)
    else:  # duck
        # Sidechain compress the bed (track 0) against the sum of tracks 1..n,
        # then mix all back together.
        followers = list(range(1, len(asset_ids)))
        if not followers:
            raise ValueError("duck mode requires at least 2 inputs (bed + 1+ ducker)")
        chain = "".join(f"[a{i}]" for i in followers)
        filter_lines.append(
            f"{chain}amix=inputs={len(followers)}:duration=longest:dropout_transition=0[duckers]"
        )
        filter_lines.append(
            "[a0][duckers]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[bed_ducked]"
        )
        filter_lines.append(
            "[bed_ducked][duckers]amix=inputs=2:duration=longest:dropout_transition=0[out]"
        )
        total = max(durations)

    filter_complex = ";".join(filter_lines)
    cmd = ["ffmpeg", "-y"]
    for p in paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=total, progress=ctx.emit_progress)

    gain_label = ""
    if any(g != 0.0 for g in gains):
        gain_label = " gains=" + ",".join(f"{g:+.1f}dB" for g in gains)
    summary = (
        f"mixed {len(asset_ids)} audio tracks ({', '.join(asset_ids)}) "
        f"mode={mode}{gain_label} -> {total:.2f}s"
    )
    record = ctx.registry.register_output(
        new_id, kind="audio", path=out_path, summary=summary, lineage=list(asset_ids),
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {
            "mode": mode,
            "track_count": len(asset_ids),
            "duration_sec": total,
            "levels_db": gains,
        },
    }


__all__ = ["dispatch"]
