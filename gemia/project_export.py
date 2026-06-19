"""Full-quality multi-track export from ProjectStore state.

Reads the current project (video + overlay + audio tracks) and produces a
final H.264/AAC MP4 via a three-pass strategy:
  pass 1 — render base video track (all enabled video clips concatenated,
            full resolution, no overlay)
  pass 2 — apply overlay track clips (image overlays + text captions) via
            a complex ffmpeg filtergraph on top of the base video
  pass 3 — build the final audio and mux it onto the composited video:
            every audio source — audio-track clips plus the embedded audio
            of video-track clips (unless the clip is muted) — is trimmed to
            its source range, gained (dB->linear), faded, positioned on the
            timeline via adelay, and mixed with amix (mirrors mix_audio).

Backward compatible: a project with no audio anywhere (no audio clips and
no video clip carrying a real, unmuted audio stream) keeps the silent
``-an`` path and exports exactly as before.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gemia.project_model import IMAGE_DURATION, normalize_project
from gemia.project_store import ProjectStore


class ProjectExportError(RuntimeError):
    """Raised when the project cannot be exported."""

    def __init__(self, code: str, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


_QUALITY_PROFILES: dict[str, dict[str, str]] = {
    "4k":    {"crf": "16", "preset": "slow"},
    "1080p": {"crf": "18", "preset": "slow"},
    "720p":  {"crf": "20", "preset": "medium"},
    "480p":  {"crf": "23", "preset": "medium"},
    "draft": {"crf": "28", "preset": "veryfast"},
}

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


# ── public entrypoint ────────────────────────────────────────────────────────


def export_project(
    store: ProjectStore,
    project_id: str,
    *,
    output_root: str | Path,
    quality: str = "1080p",
    label: str = "export",
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Export the current project state as a full-quality H.264 MP4.

    Returns a manifest dict with ``export_path``, ``duration``,
    ``resolution``, and audit metadata.
    """
    if quality not in _QUALITY_PROFILES:
        raise ProjectExportError(
            "bad_quality",
            f"unknown quality {quality!r}; valid: {', '.join(_QUALITY_PROFILES)}",
        )

    project = normalize_project(store.load(project_id))
    meta = store.load_meta(project_id)
    patch_seq = int(meta.get("patch_seq") or 0)

    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    fps = _pos(timeline.get("fps"), 30.0)
    width = max(2, int(_pos(timeline.get("width"), 1920.0)))
    height = max(2, int(_pos(timeline.get("height"), 1080.0)))
    # enforce even dimensions for yuv420p
    width = width + (width % 2)
    height = height + (height % 2)

    assets = _build_asset_map(project)
    video_clips = _enabled_video_clips(project, assets)
    overlay_clips = _enabled_overlay_clips(project, assets)
    audio_clips = _enabled_audio_clips(project, assets)

    if not video_clips:
        raise ProjectExportError(
            "no_video_clips",
            "Project has no enabled video clips to export.",
        )

    output_dir = Path(output_root).expanduser().resolve() / "exports" / project_id
    output_dir.mkdir(parents=True, exist_ok=True)
    export_id = f"{patch_seq:04d}-{_slug(label)}"
    export_path = output_dir / f"{export_id}.mp4"

    work_dir = store.renders_dir(project_id) / f"{export_id}.export-work"
    _reset_dir(work_dir)

    # The audio-inclusive master length (lumerai.patches._recompute_duration
    # already folds audio clips into timeline.duration). The composited video
    # is padded with black to reach it, so a music tail that runs past the last
    # video clip plays over black instead of an undefined over-run. For no-audio
    # / audio-shorter projects this equals the video end, so nothing changes.
    master_duration = _pos(timeline.get("duration"), 0.0)

    try:
        base_video = _render_base_video(
            video_clips, assets,
            work_dir=work_dir,
            width=width, height=height, fps=fps,
            quality=quality,
            timeout_sec=timeout_sec,
            min_duration=master_duration,
        )

        # Pass 3 input: gather every audio source first. When there is none
        # (no audio clips and no unmuted video clip with a real audio stream),
        # we keep the exact silent path below — backward compatible.
        audio_sources = _collect_audio_sources(video_clips, audio_clips, assets)

        # The composited (silent) video either goes straight to export_path
        # (no audio → current behaviour) or to a work intermediate we then mux.
        composited = (work_dir / "composited.mp4") if audio_sources else export_path
        if overlay_clips:
            _apply_overlays(
                base_video, overlay_clips, assets,
                output=composited,
                width=width, height=height, fps=fps,
                quality=quality,
                timeout_sec=timeout_sec,
            )
        elif audio_sources:
            # Reuse the silent base directly as the video to mux against.
            composited = base_video
        else:
            # No overlays, no audio — copy the base video to final location.
            _run_ffmpeg(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(base_video),
                 "-c", "copy",
                 "-movflags", "+faststart",
                 str(export_path)],
                output=export_path,
                timeout_sec=timeout_sec,
            )

        if audio_sources:
            duck_map = {
                str(t.get("id")): str(t.get("duck_under"))
                for t in (timeline.get("tracks") or [])
                if isinstance(t, dict) and t.get("duck_under")
            }
            _mux_audio_onto_video(
                composited, audio_sources,
                output=export_path,
                timeout_sec=timeout_sec,
                duck_map=duck_map,
            )
    finally:
        _cleanup_dir(work_dir)

    probe = _ffprobe(export_path)
    duration = _probe_duration(probe)
    resolution = _probe_resolution(probe) or {"width": width, "height": height}

    manifest = {
        "export_id": export_id,
        "project_id": project_id,
        "patch_seq": patch_seq,
        "export_path": str(export_path),
        "duration": duration,
        "resolution": resolution,
        "quality": quality,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video_clips_rendered": len(video_clips),
        "overlay_clips_rendered": len(overlay_clips),
        "audio_clips_rendered": len(audio_clips),
        "audio_sources_rendered": len(audio_sources),
        "has_audio": bool(audio_sources),
    }
    manifest_path = store.renders_dir(project_id) / f"{export_id}.manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


# ── pass 1: base video ───────────────────────────────────────────────────────


def _render_base_video(
    video_clips: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    *,
    work_dir: Path,
    width: int,
    height: int,
    fps: float,
    quality: str,
    timeout_sec: int,
    min_duration: float = 0.0,
) -> Path:
    """Render the base video track to an intermediate file.

    ``min_duration`` is the audio-inclusive master length: when it exceeds the
    last video clip's end, the segment list gains a trailing black gap so the
    composited video reaches the timeline end (a music tail then plays over
    black instead of running past a frozen frame).
    """
    profile = _QUALITY_PROFILES[quality]
    video_end = max(
        _pos(c["clip"].get("start"), 0.0) + _clip_duration(c["clip"]) for c in video_clips
    )
    timeline_duration = max(video_end, _pos(min_duration, 0.0))
    segments: list[dict[str, Any]] = _timeline_segments(video_clips, timeline_duration)
    segment_paths: list[Path] = []

    for index, seg in enumerate(segments, start=1):
        seg_start = _pos(seg.get("start"), 0.0)
        seg_end = _pos(seg.get("end"), seg_start)
        seg_dur = max(seg_end - seg_start, 0.0)
        if seg_dur <= 0.001:
            continue

        item = seg.get("item")
        if item is None:
            gap = work_dir / f"{index:04d}-gap.mp4"
            _render_black_segment(
                gap, duration=seg_dur, width=width, height=height, fps=fps,
                timeout_sec=timeout_sec,
            )
            segment_paths.append(gap)
            continue

        clip = item["clip"]
        asset = item["asset"]
        source = Path(str(asset.get("source_path") or "")).expanduser()
        if not source.exists():
            raise ProjectExportError(
                "source_not_found",
                f"Source file missing: {source}",
                detail=str(source),
            )

        clip_start = _pos(clip.get("start"), 0.0)
        source_in = _pos(clip.get("source_in"), 0.0) + max(seg_start - clip_start, 0.0)
        source_out = _pos(clip.get("source_out"), source_in + seg_dur)
        trim_dur = min(seg_dur, max(source_out - source_in, 0.1))

        seg_path = work_dir / f"{index:04d}-{_slug(str(clip.get('id') or 'clip'))}.mp4"
        _render_video_segment(
            source, seg_path,
            source_in=source_in, duration=trim_dur,
            width=width, height=height, fps=fps,
            profile=profile,
            timeout_sec=timeout_sec,
        )
        segment_paths.append(seg_path)

    if not segment_paths:
        raise ProjectExportError("no_segments", "No renderable video segments found.")

    base = work_dir / "base.mp4"
    if len(segment_paths) == 1:
        segment_paths[0].rename(base)
    else:
        _concat_segments(segment_paths, base, timeout_sec=timeout_sec)
    return base


def _render_video_segment(
    source: Path,
    output: Path,
    *,
    source_in: float,
    duration: float,
    width: int,
    height: int,
    fps: float,
    profile: dict[str, str],
    timeout_sec: int,
) -> None:
    vf = _video_filter(width=width, height=height, fps=fps)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{source_in:.6f}",
        "-t", f"{max(duration, 0.1):.6f}",
        "-i", str(source),
        "-an",
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", profile["crf"],
        "-preset", profile["preset"],
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output=output, timeout_sec=timeout_sec)


def _render_black_segment(
    output: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: float,
    timeout_sec: int,
) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:r={fps}:d={max(duration, 0.1):.6f}",
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output=output, timeout_sec=timeout_sec)


def _concat_segments(segments: list[Path], output: Path, *, timeout_sec: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = output.with_suffix(".concat.txt")
    list_path.write_text(
        "".join(f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for p in segments),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output=output, timeout_sec=timeout_sec)


# ── pass 2: overlay compositing ──────────────────────────────────────────────


def _apply_overlays(
    base: Path,
    overlay_clips: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    *,
    output: Path,
    width: int,
    height: int,
    fps: float,
    quality: str,
    timeout_sec: int,
) -> None:
    """Composite overlay (image + text) clips onto the base video."""
    profile = _QUALITY_PROFILES[quality]
    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    # Always first input: base video
    cmd += ["-i", str(base)]

    image_inputs: list[tuple[int, dict[str, Any], dict[str, Any]]] = []  # (input_index, clip, asset)
    text_clips: list[dict[str, Any]] = []

    input_idx = 1
    for clip in overlay_clips:
        media_kind = str(clip.get("media_kind") or "")
        if media_kind == "image":
            asset = assets.get(str(clip.get("asset_id") or ""))
            if asset is None:
                continue
            source = Path(str(asset.get("source_path") or "")).expanduser()
            if not source.exists():
                continue
            cmd += ["-i", str(source)]
            image_inputs.append((input_idx, clip, asset))
            input_idx += 1
        elif media_kind == "text":
            text_clips.append(clip)

    # Build complex filtergraph
    filter_parts: list[str] = []
    last_label = "[0:v]"

    for i, (img_idx, clip, _asset) in enumerate(image_inputs):
        start = _pos(clip.get("start"), 0.0)
        end = start + _clip_duration(clip)
        effects = clip.get("effects") if isinstance(clip.get("effects"), dict) else {}
        x = int(_pos(effects.get("x"), 0.0))
        y = int(_pos(effects.get("y"), 0.0))
        scale = _pos(effects.get("scale"), 1.0)
        opacity = min(1.0, max(0.0, _pos(effects.get("opacity"), 1.0)))
        enable_expr = f"between(t,{start:.6f},{end:.6f})"

        # Scale overlay image if needed
        img_label = f"[img{i}]"
        scale_filter = f"[{img_idx}:v]scale=iw*{scale:.4f}:-1{img_label}"
        filter_parts.append(scale_filter)

        out_label = f"[v{i}]"
        ov = (
            f"{last_label}{img_label}overlay="
            f"x={x}:y={y}:"
            f"enable='{enable_expr}'"
        )
        if opacity < 0.999:
            # Use format+colorchannelmixer to set alpha, then overlay
            alpha_label = f"[alpha{i}]"
            filter_parts[-1] = (
                f"[{img_idx}:v]scale=iw*{scale:.4f}:-1,format=rgba,"
                f"colorchannelmixer=aa={opacity:.4f}{alpha_label}"
            )
            ov = f"{last_label}{alpha_label}overlay=x={x}:y={y}:format=auto:enable='{enable_expr}'"
        filter_parts.append(f"{ov}{out_label}")
        last_label = out_label

    # Text overlays via drawtext
    for j, clip in enumerate(text_clips):
        start = _pos(clip.get("start"), 0.0)
        end = start + _clip_duration(clip)
        config = clip.get("text_config") if isinstance(clip.get("text_config"), dict) else {}
        text = str(config.get("content") or "").replace("'", "'\\''").replace(":", "\\:")
        font_size = int(max(_pos(config.get("font_size"), 64.0), 8.0))
        color_hex = str(config.get("color") or "#ffffff")
        if _HEX_COLOR_RE.match(color_hex):
            ffcolor = f"0x{color_hex[1:]}"
        else:
            ffcolor = "white"
        effects = clip.get("effects") if isinstance(clip.get("effects"), dict) else {}
        pos = config.get("position")
        if isinstance(pos, dict):
            x = int(_pos(pos.get("x"), width // 2))
            y = int(_pos(pos.get("y"), height // 2))
        else:
            x = int(_pos(effects.get("x"), width // 2))
            y = int(_pos(effects.get("y"), height // 2))

        enable_expr = f"between(t,{start:.6f},{end:.6f})"
        out_label = f"[vt{j}]"
        dt_filter = (
            f"{last_label}drawtext="
            f"text='{text}':"
            f"fontsize={font_size}:"
            f"fontcolor={ffcolor}:"
            f"x={x}:y={y}:"
            f"enable='{enable_expr}'"
            f"{out_label}"
        )
        filter_parts.append(dt_filter)
        last_label = out_label

    if filter_parts:
        cmd += ["-filter_complex", ";".join(filter_parts), "-map", last_label]
    else:
        cmd += ["-map", "0:v"]

    cmd += [
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", profile["crf"],
        "-preset", profile["preset"],
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output=output, timeout_sec=timeout_sec)


# ── pass 3: audio mix + mux ──────────────────────────────────────────────────


def _collect_audio_sources(
    video_clips: list[dict[str, Any]],
    audio_clips: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the positioned audio sources for the final mix.

    Two origins, mirroring an NLE: (a) every enabled audio-track clip, and
    (b) the embedded audio of each enabled video-track clip — kept by default
    so a talking-head clip keeps its voice — unless that clip is ``muted`` or
    its source carries no audio stream. The latter is what keeps no-audio
    projects (silent renders / testsrc2) on the silent ``-an`` path.

    Each source is ``{path, source_in, source_out, start, gain_db, fade_in,
    fade_out}`` (seconds as floats). Returns ``[]`` when there is no audio.
    """
    sources: list[dict[str, Any]] = []

    # (a) audio-track clips.
    for clip in audio_clips:
        asset = assets.get(str(clip.get("asset_id") or ""))
        if not isinstance(asset, dict):
            continue
        source = Path(str(asset.get("source_path") or "")).expanduser()
        if not source.exists():
            continue
        src = _audio_source_from_clip(clip, source)
        if src is not None:
            sources.append(src)

    # (b) embedded audio of video-track clips (default-on; muted opts out).
    probe_cache: dict[str, bool] = {}
    for item in video_clips:
        clip = item["clip"]
        effects = clip.get("effects") if isinstance(clip.get("effects"), dict) else {}
        if bool(effects.get("muted", False)):
            continue
        asset = item["asset"]
        source = Path(str(asset.get("source_path") or "")).expanduser()
        if not source.exists():
            continue
        key = str(source)
        if key not in probe_cache:
            probe_cache[key] = _source_has_audio(source)
        if not probe_cache[key]:
            continue
        src = _audio_source_from_clip(clip, source)
        if src is not None:
            sources.append(src)

    return sources


def _audio_source_from_clip(clip: dict[str, Any], source: Path) -> dict[str, Any] | None:
    source_in = _pos(clip.get("source_in"), 0.0)
    source_out = _pos(clip.get("source_out"), source_in + _clip_duration(clip))
    if source_out - source_in <= 0.001:
        return None
    effects = clip.get("effects") if isinstance(clip.get("effects"), dict) else {}
    return {
        "path": source,
        "track_id": str(clip.get("track_id") or ""),
        "source_in": source_in,
        "source_out": source_out,
        "start": _pos(clip.get("start"), 0.0),
        "gain_db": _float(effects.get("gain_db"), 0.0),
        "fade_in": max(_float(effects.get("fade_in"), 0.0), 0.0),
        "fade_out": max(_float(effects.get("fade_out"), 0.0), 0.0),
    }


def _source_has_audio(source: Path) -> bool:
    for stream in _ffprobe(source).get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "audio":
            return True
    return False


def _audio_source_chain(i: int, src: dict[str, Any]) -> str:
    """One positioned-source filter chain ending in label ``[a{i}]``.

    atrim -> (volume gain) -> (afade in/out) -> aformat fltp/48k/stereo ->
    (adelay to the clip's timeline start). Mirrors gemia/tools/mix_audio.py.
    """
    in_idx = i + 1  # input 0 is the silent video
    dur = src["source_out"] - src["source_in"]
    chain = (
        f"[{in_idx}:a]atrim=start={src['source_in']:.6f}:end={src['source_out']:.6f},"
        f"asetpts=PTS-STARTPTS"
    )
    if abs(src["gain_db"]) > 1e-6:
        chain += f",volume={_db_to_linear(src['gain_db']):.6f}"
    fade_in = min(src["fade_in"], dur)
    if fade_in > 1e-3:
        chain += f",afade=t=in:st=0:d={fade_in:.6f}"
    fade_out = min(src["fade_out"], dur)
    if fade_out > 1e-3:
        chain += f",afade=t=out:st={max(dur - fade_out, 0.0):.6f}:d={fade_out:.6f}"
    chain += ",aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    start_ms = int(round(src["start"] * 1000.0))
    if start_ms > 0:
        chain += f",adelay={start_ms}:all=1"
    return f"{chain}[a{i}]"


def _resolve_duck_map(
    duck_map: dict[str, str] | None, audio_sources: list[dict[str, Any]]
) -> dict[str, str]:
    """Keep only bed->trigger pairs where both tracks actually have audio."""
    if not duck_map:
        return {}
    have = {str(s.get("track_id") or "") for s in audio_sources if s.get("track_id")}
    return {
        bed: trigger
        for bed, trigger in duck_map.items()
        if bed in have and trigger in have and bed != trigger
    }


def _label(track_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(track_id)) or "x"


def _mux_audio_onto_video(
    video: Path,
    audio_sources: list[dict[str, Any]],
    *,
    output: Path,
    timeout_sec: int,
    duck_map: dict[str, str] | None = None,
) -> None:
    """Mix all positioned audio sources and mux onto the (silent) video.

    Without an active ducking relationship the graph is a flat
    ``amix=...:duration=longest`` over every source (identical to M6). With
    ducking, sources are grouped into per-track submixes; a bed track's submix
    is sidechain-compressed by its trigger track's submix
    (``sidechaincompress``, mirroring gemia/tools/mix_audio.py duck mode), then
    all track submixes are amix'd. Video is stream-copied; audio is AAC.
    """
    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    for src in audio_sources:
        cmd += ["-i", str(src["path"])]

    filter_parts: list[str] = [_audio_source_chain(i, src) for i, src in enumerate(audio_sources)]
    active = _resolve_duck_map(duck_map, audio_sources)

    if not active:
        # Flat path — byte-for-behaviour identical to M6 (no ducking configured).
        labels = [f"[a{i}]" for i in range(len(audio_sources))]
        if len(labels) == 1:
            filter_parts.append(f"{labels[0]}anull[aout]")
        else:
            filter_parts.append(
                "".join(labels)
                + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]"
            )
    else:
        _append_ducked_mix(filter_parts, audio_sources, active)

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output=output, timeout_sec=timeout_sec)


def _append_ducked_mix(
    filter_parts: list[str],
    audio_sources: list[dict[str, Any]],
    active: dict[str, str],
) -> None:
    """Per-track submix -> sidechain beds under triggers -> amix to [aout]."""
    # Group source indices by track (insertion order preserved).
    by_track: dict[str, list[int]] = {}
    for i, src in enumerate(audio_sources):
        by_track.setdefault(str(src.get("track_id") or ""), []).append(i)

    # Per-track raw submix -> [raw_<t>].
    for tid, idxs in by_track.items():
        raw = f"[raw_{_label(tid)}]"
        if len(idxs) == 1:
            filter_parts.append(f"[a{idxs[0]}]anull{raw}")
        else:
            filter_parts.append(
                "".join(f"[a{j}]" for j in idxs)
                + f"amix=inputs={len(idxs)}:duration=longest:dropout_transition=0{raw}"
            )

    # Split each trigger submix into a mix copy and a sidechain copy.
    triggers = set(active.values())
    premix: dict[str, str] = {}
    for tid in by_track:
        if tid in triggers:
            mix_l, sc_l = f"[mix_{_label(tid)}]", f"[sc_{_label(tid)}]"
            filter_parts.append(f"[raw_{_label(tid)}]asplit=2{mix_l}{sc_l}")
            premix[tid] = mix_l
        else:
            premix[tid] = f"[raw_{_label(tid)}]"

    # Beds: compress the bed's premix against its trigger's sidechain copy.
    final: dict[str, str] = dict(premix)
    for bed, trigger in active.items():
        duck_l = f"[duck_{_label(bed)}]"
        filter_parts.append(
            f"{premix[bed]}[sc_{_label(trigger)}]"
            "sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400"
            f"{duck_l}"
        )
        final[bed] = duck_l

    final_labels = [final[tid] for tid in by_track]
    if len(final_labels) == 1:
        filter_parts.append(f"{final_labels[0]}anull[aout]")
    else:
        filter_parts.append(
            "".join(final_labels)
            + f"amix=inputs={len(final_labels)}:duration=longest:dropout_transition=0[aout]"
        )


# ── clip / asset helpers ─────────────────────────────────────────────────────


def _build_asset_map(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(a.get("id") or a.get("asset_id") or ""): a
        for a in project.get("assets") or []
        if isinstance(a, dict)
    }


def _enabled_video_clips(
    project: dict[str, Any], assets: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    items: list[dict[str, Any]] = []
    for order, clip in enumerate(timeline.get("clips") or []):
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        if str(clip.get("media_kind") or "video") != "video":
            continue
        asset = assets.get(str(clip.get("asset_id") or ""))
        if not isinstance(asset, dict):
            continue
        items.append({"clip": clip, "asset": asset, "order": order})
    items.sort(key=lambda it: (_pos(it["clip"].get("start"), 0.0), int(it.get("order") or 0)))
    return items


def _enabled_overlay_clips(
    project: dict[str, Any], assets: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    result: list[dict[str, Any]] = []
    for clip in timeline.get("clips") or []:
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        media_kind = str(clip.get("media_kind") or "")
        if media_kind not in {"image", "text"}:
            continue
        result.append(clip)
    result.sort(key=lambda c: _pos(c.get("start"), 0.0))
    return result


def _enabled_audio_clips(
    project: dict[str, Any], assets: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Enabled audio-track clips (media_kind == 'audio') with a resolvable asset."""
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    result: list[dict[str, Any]] = []
    for clip in timeline.get("clips") or []:
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        if str(clip.get("media_kind") or "") != "audio":
            continue
        if not isinstance(assets.get(str(clip.get("asset_id") or "")), dict):
            continue
        result.append(clip)
    result.sort(key=lambda c: _pos(c.get("start"), 0.0))
    return result


def _timeline_segments(
    items: list[dict[str, Any]], timeline_duration: float
) -> list[dict[str, Any]]:
    """Return non-overlapping timeline intervals (latest-order clip wins)."""
    boundaries: set[float] = {0.0}
    max_end = 0.0
    for item in items:
        clip = item["clip"]
        start = _pos(clip.get("start"), 0.0)
        end = start + _clip_duration(clip)
        max_end = max(max_end, end)
        boundaries.add(round(start, 6))
        boundaries.add(round(end, 6))
    total = max(timeline_duration, max_end)
    boundaries.add(round(total, 6))
    ordered = sorted(b for b in boundaries if b >= 0)
    segments: list[dict[str, Any]] = []
    for start, end in zip(ordered, ordered[1:]):
        if end <= start + 0.001:
            continue
        mid = start + (end - start) / 2.0
        active = [
            it for it in items
            if _pos(it["clip"].get("start"), 0.0) <= mid
            and mid < _pos(it["clip"].get("start"), 0.0) + _clip_duration(it["clip"])
        ]
        if active:
            chosen = max(active, key=lambda it: int(it.get("order") or 0))
            segments.append({"start": start, "end": end, "item": chosen})
        else:
            segments.append({"start": start, "end": end, "item": None})
    return segments


# ── ffmpeg / probe helpers ────────────────────────────────────────────────────


def _video_filter(*, width: int, height: int, fps: float) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )


def _run_ffmpeg(cmd: list[str], output: Path, *, timeout_sec: int) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0:
        raise ProjectExportError(
            "ffmpeg_failed",
            "Export ffmpeg command failed.",
            detail=(proc.stderr or proc.stdout or "").strip()[-2000:],
        )
    if not output.exists() or output.stat().st_size <= 0:
        raise ProjectExportError("output_missing", f"ffmpeg did not create output: {output}")


def _ffprobe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return {}
    try:
        return dict(json.loads(proc.stdout or "{}"))
    except (json.JSONDecodeError, ValueError):
        return {}


def _probe_duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    return round(_pos(fmt.get("duration"), 0.0), 6)


def _probe_resolution(probe: dict[str, Any]) -> dict[str, int] | None:
    for stream in probe.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            w = int(_pos(stream.get("width"), 0))
            h = int(_pos(stream.get("height"), 0))
            if w > 0 and h > 0:
                return {"width": w, "height": h}
    return None


# ── misc helpers ─────────────────────────────────────────────────────────────


def _pos(value: Any, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(n) or n < 0:
        return float(default)
    return n


def _float(value: Any, default: float) -> float:
    """Like _pos but allows negatives (e.g. gain_db can be < 0)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return float(default)
    return n if math.isfinite(n) else float(default)


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _clip_duration(clip: dict[str, Any]) -> float:
    return max(_pos(clip.get("duration"), 0.1), 0.1)


def _slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(value).lower()).strip("-")
    return slug[:48] or "export"


def _reset_dir(path: Path) -> None:
    if path.exists():
        _cleanup_dir(path)
    path.mkdir(parents=True, exist_ok=True)


def _cleanup_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            _cleanup_dir(child)
            try:
                child.rmdir()
            except OSError:
                pass
        else:
            child.unlink(missing_ok=True)
    try:
        path.rmdir()
    except OSError:
        pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
