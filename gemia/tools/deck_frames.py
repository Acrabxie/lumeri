"""Session adapter for Deck build-state PNG materialization.

This is intentionally not a model verb yet. Phase 1b's ``assemble_deck`` will
reuse it before placing the registered images on the clip timeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from gemia.deck import (
    DeckMaterializeError,
    build_deck_pager_url,
    render_deck_frames,
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


def materialize_deck_frame_assets(
    deck: Mapping[str, Any],
    ctx: ToolContext,
    *,
    scale: int = 1,
    fail_on_overflow: bool = False,
) -> dict[str, Any]:
    """Render, write, and register all build frames for one session.

    Rendering completes in memory before any output asset is allocated, so a
    layout/font/source failure cannot leave a partial registered deck.
    """
    if not isinstance(deck, Mapping):
        raise DeckMaterializeError("deck must be a mapping")
    source_ids: list[str] = []
    for slide in deck.get("slides") or []:
        if isinstance(slide, Mapping):
            for asset_id in _image_asset_ids(slide.get("blocks")):
                if asset_id not in source_ids:
                    source_ids.append(asset_id)
    image_sources: dict[str, bytes] = {}
    for asset_id in source_ids:
        if not ctx.registry.contains(asset_id):
            raise DeckMaterializeError(
                f"deck image asset {asset_id!r} is not in this session registry"
            )
        record = ctx.registry.get(asset_id)
        if record.kind != "image":
            raise DeckMaterializeError(
                f"deck image asset {asset_id!r} is {record.kind!r}, expected 'image'"
            )
        try:
            image_sources[asset_id] = Path(record.path).read_bytes()
        except OSError as exc:
            raise DeckMaterializeError(
                f"deck image asset {asset_id!r} cannot be read"
            ) from exc

    frames = render_deck_frames(
        deck,
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
                f"deck frame {frame.slide_id}/{frame.build_id} "
                f"({frame.slide_index + 1}:{frame.build_index + 1})"
            ),
            lineage=frame.source_asset_ids,
        )
        asset_ids.append(asset_id)
        manifest.append(frame.manifest_entry(asset_id))

    pager_url = build_deck_pager_url(ctx.session_id, frames, asset_ids)
    first_build_pager_url = build_deck_pager_url(
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
        "kind": "deck",
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
    }


__all__ = ["materialize_deck_frame_assets"]
