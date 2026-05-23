"""Low-resolution preview renderer for the experimental Lumeri Runtime Kernel."""
from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project_model import normalize_project
from .project_store import ProjectStore


class ProjectRenderError(RuntimeError):
    """Raised when a stored project cannot be rendered into a preview."""

    def __init__(self, code: str, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def render_project_preview(
    store: ProjectStore,
    project_id: str,
    *,
    output_root: str | Path,
    max_long_edge: int = 640,
    label: str = "preview",
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """Render the current ProjectStore state into a low-res H.264 preview.

    The renderer is deliberately conservative for v0: it reads enabled video
    clips, sorts them by timeline start, trims each source range, pads/scales
    into a stable canvas, concatenates, probes the result, and writes an audit
    manifest under ``projects/<id>/renders``. It does not mutate timeline state.
    """
    project = normalize_project(store.load(project_id))
    meta = store.load_meta(project_id)
    patch_seq = int(meta.get("patch_seq") or 0)
    render_id = f"{patch_seq:04d}-{_safe_slug(label)}"
    render_dir = store.renders_dir(project_id)
    render_dir.mkdir(parents=True, exist_ok=True)

    output_dir = Path(output_root).expanduser().resolve() / "runtime" / project_id
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / f"{render_id}.mp4"
    work_dir = render_dir / f"{render_id}.work"
    _empty_dir(work_dir)

    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    fps = _positive_float(timeline.get("fps"), 30.0)
    target_w, target_h = _target_size(
        int(_positive_float(timeline.get("width"), 1920.0)),
        int(_positive_float(timeline.get("height"), 1080.0)),
        max_long_edge=max(int(max_long_edge), 160),
    )
    assets = {
        str(asset.get("id") or asset.get("asset_id") or ""): asset
        for asset in project.get("assets") or []
        if isinstance(asset, dict)
    }
    clips = _renderable_video_clips(project, assets)
    if not clips:
        raise ProjectRenderError(
            "no_video_clips",
            "Project has no enabled video clips to render.",
        )

    segment_paths: list[Path] = []
    source_clips: list[dict[str, Any]] = []
    timeline_duration = _positive_float(timeline.get("duration"), 0.0)
    for index, segment in enumerate(_timeline_segments(clips, timeline_duration), start=1):
        item = segment.get("item")
        seg_start = _positive_float(segment.get("start"), 0.0)
        seg_end = _positive_float(segment.get("end"), seg_start)
        seg_duration = max(seg_end - seg_start, 0.0)
        if seg_duration <= 0.001:
            continue
        if item is None:
            gap_path = work_dir / f"{index:04d}-gap.mp4"
            _render_black_segment(
                gap_path,
                duration=seg_duration,
                width=target_w,
                height=target_h,
                fps=fps,
                timeout_sec=timeout_sec,
            )
            segment_paths.append(gap_path)
            continue

        clip = item["clip"]
        asset = item["asset"]
        source = Path(str(asset.get("source_path") or "")).expanduser()
        if not source.exists():
            raise ProjectRenderError(
                "source_not_found",
                f"Source clip does not exist: {source}",
                detail=str(source),
            )
        clip_start = _positive_float(clip.get("start"), 0.0)
        clip_source_in = _positive_float(clip.get("source_in"), 0.0)
        source_in = clip_source_in + max(seg_start - clip_start, 0.0)
        source_out = _positive_float(clip.get("source_out"), source_in + seg_duration)
        trim_duration = min(seg_duration, max(source_out - source_in, 0.1))
        segment_path = work_dir / f"{index:04d}-{_safe_slug(str(clip.get('id') or 'clip'))}.mp4"
        _render_video_segment(
            source,
            segment_path,
            source_in=source_in,
            duration=trim_duration,
            width=target_w,
            height=target_h,
            fps=fps,
            timeout_sec=timeout_sec,
        )
        segment_paths.append(segment_path)
        source_clips.append(
            {
                "clip_id": str(clip.get("id") or ""),
                "asset_id": str(clip.get("asset_id") or ""),
                "source_path": str(source.resolve()),
                "timeline_start": seg_start,
                "timeline_end": seg_start + trim_duration,
                "duration": trim_duration,
                "source_in": source_in,
                "source_out": source_in + trim_duration,
            }
        )

    _concat_segments(segment_paths, preview_path, timeout_sec=timeout_sec)
    probe = ffprobe_media(preview_path)
    duration = _probe_duration(probe)
    resolution = _probe_resolution(probe) or {"width": target_w, "height": target_h}
    manifest = {
        "render_id": render_id,
        "project_id": project_id,
        "patch_seq": patch_seq,
        "preview_path": str(preview_path),
        "duration": duration,
        "resolution": resolution,
        "ffprobe": probe,
        "source_clips": source_clips,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = store.render_manifest_path(project_id, render_id)
    _write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def ffprobe_media(path: str | Path) -> dict[str, Any]:
    media_path = Path(path)
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise ProjectRenderError(
            "ffprobe_failed",
            "Rendered preview could not be probed.",
            detail=(proc.stderr or proc.stdout or "").strip()[-1200:],
        )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProjectRenderError("ffprobe_invalid_json", f"ffprobe returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectRenderError("ffprobe_invalid_json", "ffprobe JSON payload was not an object.")
    return payload


def _renderable_video_clips(project: dict[str, Any], assets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
        if str(asset.get("media_kind") or "video") != "video":
            continue
        items.append({"clip": clip, "asset": asset, "order": order})
    items.sort(key=lambda item: (_positive_float(item["clip"].get("start"), 0.0), int(item.get("order") or 0)))
    return items


def _timeline_segments(items: list[dict[str, Any]], timeline_duration: float) -> list[dict[str, Any]]:
    """Return non-overlapping timeline intervals.

    When clips overlap, v0 treats the later clip in timeline order as the
    visible/top clip. This keeps preview duration aligned with the actual
    timeline instead of concatenating overlapping clips end-to-end.
    """
    boundaries: set[float] = {0.0}
    max_end = 0.0
    for item in items:
        clip = item["clip"]
        start = _positive_float(clip.get("start"), 0.0)
        end = start + _clip_duration(clip)
        max_end = max(max_end, end)
        boundaries.add(round(start, 6))
        boundaries.add(round(end, 6))
    total = max(timeline_duration, max_end)
    boundaries.add(round(total, 6))
    ordered = sorted(boundary for boundary in boundaries if boundary >= 0)
    segments: list[dict[str, Any]] = []
    for start, end in zip(ordered, ordered[1:]):
        if end <= start + 0.001:
            continue
        midpoint = start + (end - start) / 2.0
        active = [
            item
            for item in items
            if _positive_float(item["clip"].get("start"), 0.0) <= midpoint
            and midpoint < _positive_float(item["clip"].get("start"), 0.0) + _clip_duration(item["clip"])
        ]
        if active:
            chosen = max(active, key=lambda item: int(item.get("order") or 0))
            segments.append({"start": start, "end": end, "item": chosen})
        else:
            segments.append({"start": start, "end": end, "item": None})
    return segments


def _render_video_segment(
    source: Path,
    output: Path,
    *,
    source_in: float,
    duration: float,
    width: int,
    height: int,
    fps: float,
    timeout_sec: int,
) -> None:
    vf = _video_filter(width=width, height=height, fps=fps)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{source_in:.6f}",
        "-t",
        f"{max(duration, 0.1):.6f}",
        "-i",
        str(source),
        "-an",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output, timeout_sec=timeout_sec)


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
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={width}x{height}:r={fps}:d={max(duration, 0.1):.6f}",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output, timeout_sec=timeout_sec)


def _concat_segments(segments: list[Path], output: Path, *, timeout_sec: int) -> None:
    if not segments:
        raise ProjectRenderError("no_segments", "No preview segments were produced.")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = output.with_suffix(".concat.txt")
    list_path.write_text(
        "".join(_concat_list_entry(path) for path in segments),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, output, timeout_sec=timeout_sec)


def _run_ffmpeg(cmd: list[str], output: Path, *, timeout_sec: int) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0:
        raise ProjectRenderError(
            "ffmpeg_failed",
            "Preview render failed.",
            detail=(proc.stderr or proc.stdout or "").strip()[-1600:],
        )
    if not output.exists() or output.stat().st_size <= 0:
        raise ProjectRenderError("output_missing", f"ffmpeg did not create output: {output}")


def _video_filter(*, width: int, height: int, fps: float) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )


def _target_size(width: int, height: int, *, max_long_edge: int) -> tuple[int, int]:
    width = max(int(width or 1920), 2)
    height = max(int(height or 1080), 2)
    scale = min(1.0, float(max_long_edge) / float(max(width, height)))
    target_w = max(2, int(round(width * scale)))
    target_h = max(2, int(round(height * scale)))
    if target_w % 2:
        target_w += 1
    if target_h % 2:
        target_h += 1
    return target_w, target_h


def _clip_duration(clip: dict[str, Any]) -> float:
    return max(_positive_float(clip.get("duration"), 0.1), 0.1)


def _positive_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number) or number < 0:
        return float(default)
    return number


def _probe_duration(probe: dict[str, Any]) -> float:
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    return round(_positive_float(fmt.get("duration"), 0.0), 6)


def _probe_resolution(probe: dict[str, Any]) -> dict[str, int] | None:
    for stream in probe.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            width = int(_positive_float(stream.get("width"), 0))
            height = int(_positive_float(stream.get("height"), 0))
            if width > 0 and height > 0:
                return {"width": width, "height": height}
    return None


def _safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(value).lower()).strip("-")
    return slug[:48] or "render"


def _concat_list_entry(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\").replace("'", "\\'")
    return f"file '{escaped}'\n"


def _empty_dir(path: Path) -> None:
    if path.exists():
        for child in path.iterdir():
            if child.is_dir():
                _empty_dir(child)
                child.rmdir()
            else:
                child.unlink()
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
