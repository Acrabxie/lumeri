"""assemble_deck -- materialize Deck build states onto dedicated timeline tracks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from gemia.deck import DeckMaterializeError
from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import run_ffmpeg_with_progress
from gemia.tools.deck_frames import materialize_deck_frame_assets


DECK_VIDEO_TRACK = "DECK_V1"
DECK_FRAME_TRACK = "DECK_OV1"


def _project(ctx: ToolContext):
    if ctx.project is None:
        raise ValueError("assemble_deck needs a project-backed session")
    return ctx.project


def _deck_hash(deck: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(
            deck, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeckMaterializeError(f"deck is not deterministic JSON: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()[:16]


def _valid_cached_frames(ctx: ToolContext, key: str) -> dict[str, Any] | None:
    cache = ctx.extra.get("deck_frame_cache")
    if not isinstance(cache, dict) or cache.get("key") != key:
        return None
    result = cache.get("result")
    if not isinstance(result, dict):
        return None
    for asset_id in result.get("frame_asset_ids") or []:
        if not ctx.registry.contains(str(asset_id)):
            return None
        if not ctx.registry.get(str(asset_id)).path.is_file():
            return None
    return result


async def _ensure_black_video(
    ctx: ToolContext,
    *,
    duration: float,
    width: int,
    height: int,
    fps: float,
    cache_key: str,
) -> str:
    cache = ctx.extra.get("deck_black_video_cache")
    if isinstance(cache, dict) and cache.get("key") == cache_key:
        cached_id = str(cache.get("asset_id") or "")
        if cached_id and ctx.registry.contains(cached_id) and ctx.registry.get(cached_id).path.is_file():
            return cached_id
    asset_id = ctx.registry.allocate_id("video")
    path = ctx.child_path(asset_id, ".mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=#101419:s={width}x{height}:r={fps:g}:d={duration:.6f}",
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=duration, progress=ctx.emit_progress)
    if not path.is_file() or path.stat().st_size <= 0:
        raise DeckMaterializeError("ffmpeg did not create the deck background video")
    ctx.registry.register_output(
        asset_id,
        kind="video",
        path=path,
        summary=f"deck background {width}x{height} {duration:.2f}s",
    )
    ctx.extra["deck_black_video_cache"] = {"key": cache_key, "asset_id": asset_id}
    return asset_id


def _asset_payload(ctx: ToolContext, asset_id: str, *, duration: float) -> dict[str, Any]:
    record = ctx.registry.get(asset_id)
    return {
        "id": asset_id,
        "asset_id": asset_id,
        "name": record.path.name,
        "media_kind": record.kind,
        "source_path": str(record.path),
        "duration": duration,
    }


def _insert_op(
    *,
    ctx: ToolContext,
    track_id: str,
    clip_id: str,
    asset: dict[str, Any],
    media_kind: str,
    start: float,
    duration: float,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    clip = {
        "id": clip_id,
        "asset_id": asset["asset_id"],
        "media_kind": media_kind,
        "name": asset["name"],
        "duration": round(duration, 6),
        "source_in": 0.0,
        "source_out": round(duration, 6),
        "track_id": track_id,
    }
    return {
        "op": "insert_clip",
        "data": {"asset": asset, "clip": clip},
        "track_id": track_id,
        "at": {"time": round(start, 6)},
        "ripple": False,
        "provenance": {"verb": "assemble_deck", "session_id": ctx.session_id, **provenance},
    }


def _timeline_ops(
    *,
    ctx: ToolContext,
    project_state: Mapping[str, Any],
    rendered: Mapping[str, Any],
    black_asset_id: str,
    deck_hash: str,
    total_duration: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    timeline = project_state.get("timeline") if isinstance(project_state.get("timeline"), Mapping) else {}
    tracks = [item for item in timeline.get("tracks") or [] if isinstance(item, Mapping)]
    track_by_id = {str(item.get("id") or ""): item for item in tracks}
    for track_id, expected_kind in ((DECK_VIDEO_TRACK, "video"), (DECK_FRAME_TRACK, "overlay")):
        existing = track_by_id.get(track_id)
        if existing is not None and str(existing.get("kind") or "") != expected_kind:
            raise DeckMaterializeError(
                f"dedicated track {track_id} has kind {existing.get('kind')!r}, expected {expected_kind!r}"
            )

    ops: list[dict[str, Any]] = []
    for clip in timeline.get("clips") or []:
        if isinstance(clip, Mapping) and str(clip.get("track_id") or "") in {
            DECK_VIDEO_TRACK, DECK_FRAME_TRACK,
        }:
            ops.append({"op": "delete_clip", "clip_id": str(clip.get("id") or "")})
    if DECK_VIDEO_TRACK not in track_by_id:
        ops.append({"op": "add_track", "kind": "video", "track_id": DECK_VIDEO_TRACK, "name": "Deck Base"})
    if DECK_FRAME_TRACK not in track_by_id:
        ops.append({"op": "add_track", "kind": "overlay", "track_id": DECK_FRAME_TRACK, "name": "Deck Frames"})

    clip_ids: list[str] = []
    background_clip_id = f"deck_{deck_hash}_background"
    ops.append(_insert_op(
        ctx=ctx,
        track_id=DECK_VIDEO_TRACK,
        clip_id=background_clip_id,
        asset=_asset_payload(ctx, black_asset_id, duration=total_duration),
        media_kind="video",
        start=0.0,
        duration=total_duration,
        provenance={"deck_hash": deck_hash, "role": "background"},
    ))
    clip_ids.append(background_clip_id)

    cursor = 0.0
    for index, frame in enumerate(rendered.get("frames") or []):
        if not isinstance(frame, Mapping):
            raise DeckMaterializeError("rendered frame manifest is malformed")
        asset_id = str(frame.get("asset_id") or "")
        dwell = float(frame.get("dwell_sec") or 0.0)
        if dwell <= 0:
            raise DeckMaterializeError(f"frame {index} has non-positive dwell")
        clip_id = f"deck_{deck_hash}_frame_{index:04d}"
        ops.append(_insert_op(
            ctx=ctx,
            track_id=DECK_FRAME_TRACK,
            clip_id=clip_id,
            asset=_asset_payload(ctx, asset_id, duration=dwell),
            media_kind="image",
            start=cursor,
            duration=dwell,
            provenance={
                "deck_hash": deck_hash,
                "slide_id": str(frame.get("slide_id") or ""),
                "build_id": str(frame.get("build_id") or ""),
            },
        ))
        clip_ids.append(clip_id)
        cursor += dwell
    return ops, clip_ids


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project = _project(ctx)
    state = project.load()
    deck = state.get("deck")
    if not isinstance(deck, Mapping) or not deck.get("slides"):
        raise DeckMaterializeError("deck is empty — call draft_deck or set_deck first")
    timeline = state.get("timeline") if isinstance(state.get("timeline"), Mapping) else {}
    width = int(timeline.get("width") or 1920)
    height = int(timeline.get("height") or 1080)
    fps = float(timeline.get("fps") or 30.0)
    if (width, height) != (1920, 1080):
        raise DeckMaterializeError(
            f"deck v1 timeline must be 1920x1080, got {width}x{height}"
        )
    strict = bool(args.get("fail_on_overflow", False))
    digest = _deck_hash(deck)
    cache_key = f"{digest}:1x"
    rendered = _valid_cached_frames(ctx, cache_key)
    if rendered is None:
        rendered = materialize_deck_frame_assets(
            deck, ctx, scale=1, fail_on_overflow=strict
        )
        ctx.extra["deck_frame_cache"] = {"key": cache_key, "result": rendered}
    elif strict and rendered.get("overflow"):
        raise DeckMaterializeError("cached deck frames contain overflow; refine the copy")
    frames = rendered.get("frames") or []
    if not frames:
        raise DeckMaterializeError("deck produced no build frames")
    total_duration = sum(float(frame.get("dwell_sec") or 0.0) for frame in frames)
    if total_duration <= 0:
        raise DeckMaterializeError("deck total dwell must be positive")
    black_key = f"{digest}:{width}x{height}:{fps:g}:{total_duration:.6f}"
    black_asset_id = await _ensure_black_video(
        ctx,
        duration=total_duration,
        width=width,
        height=height,
        fps=fps,
        cache_key=black_key,
    )
    ops, clip_ids = _timeline_ops(
        ctx=ctx,
        project_state=state,
        rendered=rendered,
        black_asset_id=black_asset_id,
        deck_hash=digest,
        total_duration=total_duration,
    )
    result = project.apply_ops(ops, label="assemble_deck")
    transitions = [
        str(slide.get("id") or "")
        for slide in deck.get("slides") or []
        if isinstance(slide, Mapping)
        and str((slide.get("transition") or {}).get("kind") or "cut") == "fade"
    ]
    return {
        **rendered,
        "assembled": True,
        "seq": result.get("patch_seq_end"),
        "deck_hash": digest,
        "background_asset_id": black_asset_id,
        "clip_ids": clip_ids,
        "total_duration_sec": total_duration,
        "timeline": project.compact_text(),
        "degradations": (
            [{"kind": "fade_to_cut", "slide_ids": transitions}]
            if transitions else []
        ),
        "summary": (
            f"assembled {rendered.get('slide_count')} slide(s) / "
            f"{rendered.get('frame_count')} build state(s) onto the timeline "
            f"({total_duration:.1f}s)"
        ),
    }


__all__ = ["DECK_FRAME_TRACK", "DECK_VIDEO_TRACK", "dispatch"]
