"""Session adapter for Quanta build-state PNG materialization.

``assemble_quanta`` reuses this adapter before placing the registered images on
the clip timeline.  Keeping rasterization here also gives ``refine_quantum`` one
place to rematerialize slide changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from gemia.quanta import (
    QuantaMaterializeError,
    build_quanta_pager_url,
    build_quanta_pager_url_from_manifest,
    render_quanta_frames,
)
from gemia.tools._context import ToolContext


def _image_asset_ids(blocks: Any) -> list[str]:
    result: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, Mapping):
            continue
        if block.get("kind") == "group":
            result.extend(_image_asset_ids(block.get("children")))
        elif block.get("kind") == "image" and block.get("asset_id"):
            asset_id = str(block["asset_id"])
            if asset_id not in result:
                result.append(asset_id)
    return result


def materialize_quanta_frame_assets(
    quanta: Mapping[str, Any],
    ctx: ToolContext,
    *,
    scale: int = 1,
    fail_on_overflow: bool = False,
) -> dict[str, Any]:
    """Render, write, and register all build frames for one session.

    Rendering completes in memory before any output asset is allocated, so a
    layout/font/source failure cannot leave a partial registered quanta.
    """
    if not isinstance(quanta, Mapping):
        raise QuantaMaterializeError("quanta must be a mapping")
    source_ids: list[str] = []
    for slide in quanta.get("slides") or []:
        if isinstance(slide, Mapping):
            for asset_id in _image_asset_ids(slide.get("blocks")):
                if asset_id not in source_ids:
                    source_ids.append(asset_id)
    image_sources: dict[str, bytes] = {}
    for asset_id in source_ids:
        if not ctx.registry.contains(asset_id):
            raise QuantaMaterializeError(
                f"quanta image asset {asset_id!r} is not in this session registry"
            )
        record = ctx.registry.get(asset_id)
        if record.kind != "image":
            raise QuantaMaterializeError(
                f"quanta image asset {asset_id!r} is {record.kind!r}, expected 'image'"
            )
        try:
            image_sources[asset_id] = Path(record.path).read_bytes()
        except OSError as exc:
            raise QuantaMaterializeError(
                f"quanta image asset {asset_id!r} cannot be read"
            ) from exc

    frames = render_quanta_frames(
        quanta,
        image_sources=image_sources,
        scale=scale,
        fail_on_overflow=fail_on_overflow,
    )
    output_dir = Path(ctx.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_ids: list[str] = []
    manifest: list[dict[str, Any]] = []
    for frame in frames:
        asset_id = ctx.registry.allocate_id("image")
        path = ctx.child_path(asset_id, ".png")
        path.write_bytes(frame.png_bytes)
        ctx.registry.register_output(
            asset_id,
            kind="image",
            path=path,
            summary=(
                f"quanta frame {frame.slide_id}/{frame.build_id} "
                f"({frame.slide_index + 1}:{frame.build_index + 1})"
            ),
            lineage=frame.source_asset_ids,
        )
        asset_ids.append(asset_id)
        manifest.append(frame.manifest_entry(asset_id))

    pager_url = build_quanta_pager_url(ctx.session_id, frames, asset_ids)
    first_build_pager_url = build_quanta_pager_url(
        ctx.session_id, frames, asset_ids, first_build_only=True
    )
    overflow = [
        {
            "slide_id": frame.slide_id,
            "build_id": frame.build_id,
            "items": [dict(item) for item in frame.overflow],
        }
        for frame in frames
        if frame.overflow
    ]
    return {
        "kind": "quanta",
        "asset_id": asset_ids[0] if asset_ids else None,
        "frame_asset_ids": asset_ids,
        "frames": manifest,
        "pager_url": pager_url,
        "first_build_pager_url": first_build_pager_url,
        "slide_count": len({frame.slide_id for frame in frames}),
        "frame_count": len(frames),
        "overflow": overflow,
        "summary": (
            f"rendered {len({frame.slide_id for frame in frames})} slide(s) / "
            f"{len(frames)} build state(s)"
        ),
        "rematerialization_scope": "quanta",
    }


def _ordered_slides(quanta: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    slides = [
        slide for slide in quanta.get("slides") or [] if isinstance(slide, Mapping)
    ]
    by_id = {str(slide.get("id") or ""): slide for slide in slides}
    path = [str(value or "") for value in quanta.get("default_path") or []]
    if not path:
        path = list(by_id)
    if not path or len(path) != len(by_id) or set(path) != set(by_id):
        raise QuantaMaterializeError("quanta.default_path must cover every slide exactly once")
    return [by_id[slide_id] for slide_id in path]


def _expected_builds(slide: Mapping[str, Any]) -> list[tuple[str, float]]:
    builds = [
        build for build in slide.get("builds") or [] if isinstance(build, Mapping)
    ]
    if not builds:
        return [("b1", 3.0)]
    return [
        (str(build.get("id") or "b1"), float(build.get("dwell_sec") or 0.0))
        for build in builds
    ]


def _reusable_frames(
    previous: Mapping[str, Any] | None,
    ctx: ToolContext,
    slides: list[Mapping[str, Any]],
    changed_slide_id: str,
) -> dict[str, list[dict[str, Any]]] | None:
    if not isinstance(previous, Mapping):
        return None
    raw_frames = previous.get("frames")
    if not isinstance(raw_frames, list):
        return None
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_frames:
        if not isinstance(raw, Mapping):
            return None
        frame = dict(raw)
        slide_id = str(frame.get("slide_id") or "")
        asset_id = str(frame.get("asset_id") or "")
        if not slide_id or not asset_id or not ctx.registry.contains(asset_id):
            return None
        if not ctx.registry.get(asset_id).path.is_file():
            return None
        grouped.setdefault(slide_id, []).append(frame)
    for slide in slides:
        slide_id = str(slide.get("id") or "")
        if slide_id == changed_slide_id:
            continue
        actual = sorted(
            grouped.get(slide_id) or [],
            key=lambda frame: int(frame.get("build_index", -1)),
        )
        expected = _expected_builds(slide)
        if len(actual) != len(expected):
            return None
        for index, (frame, (build_id, dwell)) in enumerate(zip(actual, expected)):
            if int(frame.get("build_index", -1)) != index:
                return None
            if str(frame.get("build_id") or "") != build_id:
                return None
            if abs(float(frame.get("dwell_sec") or 0.0) - dwell) > 1e-6:
                return None
        grouped[slide_id] = actual
    return grouped


def rematerialize_quanta_slide_assets(
    quanta: Mapping[str, Any],
    ctx: ToolContext,
    *,
    slide_id: str,
    previous: Mapping[str, Any] | None,
    scale: int = 1,
    fail_on_overflow: bool = False,
) -> dict[str, Any]:
    """Render one changed slide and reuse every unchanged registered frame.

    If the prior frame manifest is missing or stale, this safely falls back to
    whole-quanta materialization; callers can surface the returned scope.
    """
    slides = _ordered_slides(quanta)
    target = next(
        (slide for slide in slides if str(slide.get("id") or "") == slide_id),
        None,
    )
    if target is None:
        raise QuantaMaterializeError(f"slide not found: {slide_id!r}")
    reusable = _reusable_frames(previous, ctx, slides, slide_id)
    if reusable is None:
        return materialize_quanta_frame_assets(
            quanta, ctx, scale=scale, fail_on_overflow=fail_on_overflow
        )
    if fail_on_overflow:
        for other_id, frames in reusable.items():
            if other_id != slide_id and any(frame.get("overflow") for frame in frames):
                raise QuantaMaterializeError(
                    f"cached slide {other_id} contains overflow; refine that copy first"
                )

    subquanta = dict(quanta)
    subquanta["slides"] = [target]
    subquanta["default_path"] = [slide_id]
    changed = materialize_quanta_frame_assets(
        subquanta, ctx, scale=scale, fail_on_overflow=fail_on_overflow
    )
    changed_frames = [
        dict(frame) for frame in changed.get("frames") or []
        if isinstance(frame, Mapping)
    ]
    manifest: list[dict[str, Any]] = []
    for slide_index, slide in enumerate(slides):
        current_id = str(slide.get("id") or "")
        source = changed_frames if current_id == slide_id else reusable[current_id]
        for build_index, source_frame in enumerate(source):
            frame = dict(source_frame)
            frame["slide_index"] = slide_index
            frame["build_index"] = build_index
            manifest.append(frame)

    asset_ids = [str(frame.get("asset_id") or "") for frame in manifest]
    overflow = [
        {
            "slide_id": str(frame.get("slide_id") or ""),
            "build_id": str(frame.get("build_id") or ""),
            "items": [dict(item) for item in frame.get("overflow") or []],
        }
        for frame in manifest
        if frame.get("overflow")
    ]
    return {
        "kind": "quanta",
        "asset_id": asset_ids[0] if asset_ids else None,
        "frame_asset_ids": asset_ids,
        "frames": manifest,
        "pager_url": build_quanta_pager_url_from_manifest(ctx.session_id, manifest),
        "first_build_pager_url": build_quanta_pager_url_from_manifest(
            ctx.session_id, manifest, first_build_only=True
        ),
        "slide_count": len(slides),
        "frame_count": len(manifest),
        "overflow": overflow,
        "summary": (
            f"rematerialized slide {slide_id} and reused "
            f"{len(slides) - 1} unchanged slide(s)"
        ),
        "rematerialization_scope": "slide",
        "rematerialized_slide_id": slide_id,
    }


__all__ = ["materialize_quanta_frame_assets", "rematerialize_quanta_slide_assets"]
