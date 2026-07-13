"""refine_quantum -- revise and rematerialize one assembled Quanta slide."""
from __future__ import annotations

from typing import Any, Mapping

from gemia.tools._context import ToolContext
from gemia.tools.quanta_frames import rematerialize_quanta_slide_assets


def _project(ctx: ToolContext):
    if ctx.project is None:
        raise ValueError("refine_quantum needs a project-backed session")
    return ctx.project


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    # Lazy imports avoid coupling the dispatcher registry back into this verb.
    from gemia.tools import assemble_quanta as _assemble
    from gemia.tools import quanta as _quanta

    project = _project(ctx)
    slide_id = str(args.get("slide_id") or "").strip()
    if not slide_id:
        raise ValueError("refine_quantum requires 'slide_id'")
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("refine_quantum requires a non-empty 'fields' object")

    before = project.load().get("quanta")
    if not isinstance(before, Mapping) or not before.get("slides"):
        raise ValueError("quanta is empty — call draft_quanta or set_quanta first")
    old_cache = ctx.extra.get("quanta_frame_cache")
    previous = None
    if (
        isinstance(old_cache, Mapping)
        and old_cache.get("key") == _assemble.quanta_frame_cache_key(before)
        and isinstance(old_cache.get("result"), Mapping)
    ):
        previous = old_cache["result"]

    updated = await _quanta.dispatch_update_quantum(
        {"slide_id": slide_id, "fields": fields}, ctx
    )
    current = project.load().get("quanta")
    if not isinstance(current, Mapping):
        raise ValueError("updated quanta is unavailable")

    # An update changes the quanta digest. Seed the new cache with a selective
    # render when possible so assemble_quanta can rebuild the timeline without
    # rasterizing unchanged slides again.
    ctx.extra.pop("quanta_frame_cache", None)
    rendered = rematerialize_quanta_slide_assets(
        current,
        ctx,
        slide_id=slide_id,
        previous=previous,
        scale=1,
        fail_on_overflow=bool(args.get("fail_on_overflow", False)),
    )
    ctx.extra["quanta_frame_cache"] = {
        "key": _assemble.quanta_frame_cache_key(current),
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
        "quanta": updated.get("quanta"),
        "summary": (
            f"refined slide {slide_id}, {rendered.get('summary')}, and rebuilt "
            f"{assembled.get('frame_count')} timeline build state(s)"
        ),
    }


__all__ = ["dispatch"]
