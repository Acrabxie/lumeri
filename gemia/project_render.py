"""Low-resolution preview renderer for the experimental Lumeri Runtime Kernel."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .compat import ffmpeg_path, ffprobe_path
from .project_model import normalize_project
from .project_store import ProjectStore


class ProjectRenderError(RuntimeError):
    """Raised when a stored project cannot be rendered into a preview."""

    def __init__(self, code: str, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


_PREVIEW_CACHE_SCHEMA = "lumeri.preview-segment-cache.v1"
_PREVIEW_CACHE_RENDERER_REVISION = 1
_PREVIEW_CACHE_LOCKS_GUARD = threading.Lock()
_PREVIEW_CACHE_LOCKS: dict[tuple[str, str], tuple[threading.Lock, int]] = {}


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
    cache_root = output_dir.parent / ".preview-segment-cache" / "v1"
    preview_path = output_dir / f"{render_id}.mp4"
    work_dir = render_dir / f"{render_id}.work"
    _empty_dir(work_dir)
    cache_stats = _empty_cache_stats()
    source_fingerprints: dict[tuple[str, int, int], dict[str, Any]] = {}

    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    # Segment commands format time and rate to six decimals; canonicalise the
    # same way before cache-keying so equal keys always mean equal ffmpeg args.
    fps = round(_positive_float(timeline.get("fps"), 30.0), 6)
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
            cache_spec = _preview_segment_spec(
                source={"kind": "generated-black"},
                source_in=0.0,
                duration=seg_duration,
                width=target_w,
                height=target_h,
                fps=fps,
                media_kind="gap",
            )
            cached_gap = _materialize_preview_segment(
                cache_root=cache_root,
                fallback_path=gap_path,
                cache_spec=cache_spec,
                stats=cache_stats,
                render=lambda output: _render_black_segment(
                    output,
                    duration=seg_duration,
                    width=target_w,
                    height=target_h,
                    fps=fps,
                    timeout_sec=timeout_sec,
                ),
            )
            segment_paths.append(cached_gap)
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
        media_kind = str(asset.get("media_kind") or "video")
        source_fingerprint = _source_fingerprint(source, source_fingerprints)
        cache_spec = _preview_segment_spec(
            source=source_fingerprint,
            source_in=source_in,
            duration=trim_duration,
            width=target_w,
            height=target_h,
            fps=fps,
            media_kind=media_kind,
        )
        cached_segment = _materialize_preview_segment(
            cache_root=cache_root,
            fallback_path=segment_path,
            cache_spec=cache_spec,
            stats=cache_stats,
            render=lambda output: _render_video_segment(
                source,
                output,
                source_in=source_in,
                duration=trim_duration,
                width=target_w,
                height=target_h,
                fps=fps,
                media_kind=media_kind,
                timeout_sec=timeout_sec,
            ),
        )
        segment_paths.append(cached_segment)
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
        # Deliberately high-level: callers can measure reuse without learning
        # cache paths, source fingerprints, or recovery details.
        "segment_cache": _finalize_cache_stats(cache_stats),
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
            ffprobe_path(),
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


def _empty_cache_stats() -> dict[str, int]:
    return {
        "segments_total": 0,
        "hits": 0,
        "misses": 0,
        "rebuilds": 0,
        "bypassed": 0,
    }


def _finalize_cache_stats(stats: dict[str, int]) -> dict[str, Any]:
    total = max(int(stats.get("segments_total") or 0), 0)
    hits = max(int(stats.get("hits") or 0), 0)
    return {
        "schema": _PREVIEW_CACHE_SCHEMA,
        "segments_total": total,
        "hits": hits,
        "misses": max(int(stats.get("misses") or 0), 0),
        "rebuilds": max(int(stats.get("rebuilds") or 0), 0),
        "bypassed": max(int(stats.get("bypassed") or 0), 0),
        "hit_ratio": round(hits / total, 6) if total else 0.0,
    }


def _source_fingerprint(
    source: Path,
    memo: dict[tuple[str, int, int], dict[str, Any]],
) -> dict[str, Any]:
    """Return a content fingerprint without persisting the source path.

    A stat tuple is only an in-process memo key.  The content-addressed cache
    key itself uses the full file SHA-256 and byte count, so changing a source
    invalidates the segment even when the project patch sequence does not.
    """
    resolved = source.expanduser().resolve()
    stat = resolved.stat()
    memo_key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
    cached = memo.get(memo_key)
    if cached is not None:
        return dict(cached)
    fingerprint = {
        "kind": "file",
        "sha256": _sha256_file(resolved),
        "bytes": int(stat.st_size),
    }
    memo[memo_key] = fingerprint
    return dict(fingerprint)


def _preview_segment_spec(
    *,
    source: dict[str, Any],
    source_in: float,
    duration: float,
    width: int,
    height: int,
    fps: float,
    media_kind: str,
) -> dict[str, Any]:
    """Canonical material inputs for one proxy fragment.

    Clip ids, project ids, labels, and patch sequence are intentionally absent:
    they do not affect pixels and therefore must not invalidate proxy reuse.
    """
    return {
        "schema": _PREVIEW_CACHE_SCHEMA,
        "renderer_revision": _PREVIEW_CACHE_RENDERER_REVISION,
        "source": source,
        "source_in": round(float(source_in), 6),
        "duration": round(float(duration), 6),
        "width": int(width),
        "height": int(height),
        "fps": round(float(fps), 6),
        "media_kind": str(media_kind),
        "video_filter": "scale-pad-setsar-fps-yuv420p",
        "video_encoder": "libx264-veryfast-crf28",
    }


def _preview_segment_cache_key(cache_spec: dict[str, Any]) -> str:
    encoded = json.dumps(
        cache_spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _materialize_preview_segment(
    *,
    cache_root: Path,
    fallback_path: Path,
    cache_spec: dict[str, Any],
    stats: dict[str, int],
    render: Callable[[Path], None],
) -> Path:
    """Return a valid cached segment, rebuilding or bypassing transparently.

    Cache metadata never contains a source path.  Corrupt/missing entries are
    regenerated.  If the cache filesystem itself is unavailable, rendering
    falls back to the per-render work directory instead of surfacing a cache
    implementation error to the product caller.
    """
    stats["segments_total"] = int(stats.get("segments_total") or 0) + 1
    key = _preview_segment_cache_key(cache_spec)
    cache_dir = cache_root / key[:2]
    cache_path = cache_dir / f"{key}.mp4"
    metadata_path = cache_dir / f"{key}.json"
    lock = _preview_segment_lock(cache_root, key)
    with lock:
        # Recheck inside the per-key lock. Two renders that arrive together for
        # the same material must not both encode the same segment.
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            entry_existed = cache_path.exists() or metadata_path.exists()
            if _valid_cached_segment(cache_path, metadata_path, key):
                stats["hits"] = int(stats.get("hits") or 0) + 1
                return cache_path
        except (OSError, ValueError, json.JSONDecodeError):
            stats["misses"] = int(stats.get("misses") or 0) + 1
            stats["bypassed"] = int(stats.get("bypassed") or 0) + 1
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            render(fallback_path)
            return fallback_path

        stats["misses"] = int(stats.get("misses") or 0) + 1
        if entry_existed:
            stats["rebuilds"] = int(stats.get("rebuilds") or 0) + 1

        # The product render is created in its ordinary per-render work path
        # first. Cache publication is only a best-effort copy after successful
        # encoding, so a cache filesystem problem cannot turn into a render
        # failure or force a second encode.
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        render(fallback_path)
        media_sha256 = _sha256_file(fallback_path)
        media_bytes = int(fallback_path.stat().st_size)
        token = uuid.uuid4().hex
        media_temp = cache_dir / f".{key}.{token}.tmp.mp4"
        metadata_temp = cache_dir / f".{key}.{token}.tmp.json"
        try:
            shutil.copyfile(fallback_path, media_temp)
            media_temp.replace(cache_path)
            metadata_temp.write_text(
                json.dumps(
                    {
                        "schema": _PREVIEW_CACHE_SCHEMA,
                        "key": key,
                        "media_sha256": media_sha256,
                        "media_bytes": media_bytes,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            metadata_temp.replace(metadata_path)
            if not _valid_cached_segment(cache_path, metadata_path, key):
                stats["bypassed"] = int(stats.get("bypassed") or 0) + 1
        except (OSError, ValueError, json.JSONDecodeError):
            stats["bypassed"] = int(stats.get("bypassed") or 0) + 1
        finally:
            for temp_path in (media_temp, metadata_temp):
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass
        return fallback_path


@contextmanager
def _preview_segment_lock(cache_root: Path, key: str) -> Iterator[None]:
    """Serialize one cache key without retaining locks after its last user."""
    identity = (str(cache_root.absolute()), key)
    with _PREVIEW_CACHE_LOCKS_GUARD:
        state = _PREVIEW_CACHE_LOCKS.get(identity)
        lock, users = state if state is not None else (threading.Lock(), 0)
        _PREVIEW_CACHE_LOCKS[identity] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _PREVIEW_CACHE_LOCKS_GUARD:
            current_lock, users = _PREVIEW_CACHE_LOCKS[identity]
            if users <= 1:
                _PREVIEW_CACHE_LOCKS.pop(identity, None)
            else:
                _PREVIEW_CACHE_LOCKS[identity] = (current_lock, users - 1)


def _valid_cached_segment(cache_path: Path, metadata_path: Path, key: str) -> bool:
    try:
        if not cache_path.is_file() or not metadata_path.is_file():
            return False
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return False
        if metadata.get("schema") != _PREVIEW_CACHE_SCHEMA or metadata.get("key") != key:
            return False
        size = int(metadata.get("media_bytes") or 0)
        if size <= 0 or cache_path.stat().st_size != size:
            return False
        expected_sha = str(metadata.get("media_sha256") or "")
        return len(expected_sha) == 64 and _sha256_file(cache_path) == expected_sha
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _renderable_video_clips(project: dict[str, Any], assets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = project.get("timeline") if isinstance(project.get("timeline"), dict) else {}
    
    # Collect video track ids
    video_track_ids = set()
    for track in timeline.get("tracks") or []:
        if isinstance(track, dict) and str(track.get("kind")) == "video":
            video_track_ids.add(str(track.get("id") or ""))
    
    items: list[dict[str, Any]] = []
    for order, clip in enumerate(timeline.get("clips") or []):
        if not isinstance(clip, dict) or not bool(clip.get("enabled", True)):
            continue
        # Only process clips on video tracks
        clip_track_id = str(clip.get("track_id") or "")
        if clip_track_id not in video_track_ids:
            continue
        # Accept video and image media kinds
        clip_media_kind = str(clip.get("media_kind") or "video")
        if clip_media_kind not in {"video", "image"}:
            continue
        asset_id = str(clip.get("asset_id") or "")
        asset = assets.get(asset_id) if asset_id else None
        # For media clips, we need an asset; verify media kind if present
        if not asset_id and clip_media_kind in {"video", "image"}:
            continue  # Media clip without asset_id can't be rendered
        if asset and str(asset.get("media_kind") or "video") not in {"video", "image"}:
            continue  # Asset type mismatch
        # Use asset or construct minimal info from clip
        asset = asset or {
            "id": asset_id,
            "asset_id": asset_id,
            "media_kind": clip_media_kind,
            "source_path": str(clip.get("source_path", "")),
        }
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
    media_kind: str = "video",
    timeout_sec: int,
) -> None:
    vf = _video_filter(width=width, height=height, fps=fps)
    
    # For image sources, use -loop 1 to hold the frame
    cmd = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    
    if media_kind == "image":
        cmd.extend([
            "-loop",
            "1",
            "-framerate",
            f"{fps:.6f}",
            "-t",
            f"{max(duration, 0.1):.6f}",
            "-i",
            str(source),
        ])
    else:
        # video: use standard -ss, -t trimming
        cmd.extend([
            "-ss",
            f"{source_in:.6f}",
            "-t",
            f"{max(duration, 0.1):.6f}",
            "-i",
            str(source),
        ])
    
    cmd.extend([
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
    ])
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
        ffmpeg_path(),
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
        ffmpeg_path(),
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
