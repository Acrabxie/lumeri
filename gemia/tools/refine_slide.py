"""refine_slide -- revise and rematerialize one assembled Deck slide."""
from __future__ import annotations

from typing import Any, Mapping

from gemia.tools._context import ToolContext
from gemia.tools.deck_frames import rematerialize_deck_slide_assets


def _project(ctx: ToolContext):
    if ctx.project is None:
        raise ValueError("refine_slide needs a project-backed session")
    return ctx.project


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    # Lazy imports avoid coupling the dispatcher registry back into this verb.
    from gemia.tools import assemble_deck as _assemble
    from gemia.tools import deck as _deck

    project = _project(ctx)
    slide_id = str(args.get("slide_id") or "").strip()
    if not slide_id:
        raise ValueError("refine_slide requires 'slide_id'")
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("refine_slide requires a non-empty 'fields' object")

    before = project.load().get("deck")
    if not isinstance(before, Mapping) or not before.get("slides"):
        raise ValueError("deck is empty — call draft_deck or set_deck first")
    old_cache = ctx.extra.get("deck_frame_cache")
    previous = None
    if (
        isinstance(old_cache, Mapping)
        and old_cache.get("key") == _assemble.deck_frame_cache_key(before)
        and isinstance(old_cache.get("result"), Mapping)
    ):
        previous = old_cache["result"]

    updated = await _deck.dispatch_update_slide(
        {"slide_id": slide_id, "fields": fields}, ctx
    )
    current = project.load().get("deck")
    if not isinstance(current, Mapping):
        raise ValueError("updated deck is unavailable")

    # An update changes the deck digest. Seed the new cache with a selective
    # render when possible so assemble_deck can rebuild the timeline without
    # rasterizing unchanged slides again.
    ctx.extra.pop("deck_frame_cache", None)
    rendered = rematerialize_deck_slide_assets(
        current,
        ctx,
        slide_id=slide_id,
        previous=previous,
        scale=1,
        fail_on_overflow=bool(args.get("fail_on_overflow", False)),
    )
    ctx.extra["deck_frame_cache"] = {
        "key": _assemble.deck_frame_cache_key(current),
        "result": rendered,
    }
    assembled = await _assemble.dispatch(
        {"fail_on_overflow": bool(args.get("fail_on_overflow", False))}, ctx
    )
    return {
        **assembled,
        "refined": True,
        "updated_slide": slide_id,
        "update_seq": updated.get("seq"),
        "deck": updated.get("deck"),
        "summary": (
            f"refined slide {slide_id}, {rendered.get('summary')}, and rebuilt "
            f"{assembled.get('frame_count')} timeline build state(s)"
        ),
    }


__all__ = ["dispatch"]
