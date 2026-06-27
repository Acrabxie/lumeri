"""lumen_*: lumenframe layer editing verbs with persistent storage.

This module exposes the lumenframe LayerPatch vocabulary as agent verbs
(similar to timeline_* verbs). The document state is persisted to the project
and survives across session boundaries.

Design contract:
- Each verb compiles to a LayerPatch and applies it atomically via
  ``apply_layer_patch``.
- ``get_lumenframe`` returns a compact summary of the current doc state
  (layer tree, selection).
- ``lumen_patch`` is the low-level verb that accepts a raw ops list;
  convenience verbs (add_layer, set_transform, etc.) are built on top.
- **Persistence**: if ctx.project (ProjectHandle) is available, doc is stored
  in project["lumenframe"] field and persists across sessions. Fallback to
  in-memory cache for non-project sessions. Lumenframe is **orthogonal**
  to timeline — no mutation of timeline fields.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from gemia.tools._context import ToolContext

try:
    from lumenframe import (
        empty_doc,
        apply_layer_patch,
        describe_ops,
        new_layer,
        find_layer,
        find_parent,
        normalize_doc,
    )
    from lumenframe.compile import compile_to_layer_stack
except ImportError:
    # Graceful fallback if lumenframe is not available
    empty_doc = None
    apply_layer_patch = None
    describe_ops = None
    new_layer = None
    find_layer = None
    find_parent = None
    normalize_doc = None
    compile_to_layer_stack = None


# Session-local doc state cache (keyed by session_id) — fallback for non-project sessions
_DOC_CACHE: dict[str, dict[str, Any]] = {}


def _lumenframe_file_path(ctx: ToolContext) -> Path | None:
    """Get the path to the lumenframe.json file for this project, if applicable."""
    if ctx.project is None:
        return None
    try:
        project_dir = ctx.project.store.project_dir(ctx.project.project_id)
        return project_dir / "lumenframe.json"
    except Exception:
        return None


def _lumendoc(ctx: ToolContext) -> dict[str, Any]:
    """Retrieve or initialize the session's lumenframe document.

    **Persistence strategy:**
    - If ctx.project (ProjectHandle) is available: load from <project_dir>/lumenframe.json,
      lazy-initialize and persist if missing. This keeps lumenframe orthogonal to
      the main project state dict (timeline, assets, etc.).
    - Else: fall back to in-session memory cache (_DOC_CACHE)

    This ensures lumenframe docs persist across session boundaries (like timeline)
    while remaining fully orthogonal to timeline structure.
    """
    if empty_doc is None or normalize_doc is None:
        raise RuntimeError("lumenframe module not available")

    # Prefer persistent project storage if available
    if ctx.project is not None:
        file_path = _lumenframe_file_path(ctx)
        if file_path is not None:
            try:
                if file_path.exists():
                    # Load from file
                    raw = json.loads(file_path.read_text(encoding="utf-8"))
                    doc = normalize_doc(raw)
                    return doc
                else:
                    # Lazy initialize: create, save, and return
                    doc = empty_doc()
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                    return doc
            except Exception:
                # If file-based loading fails, fall back to memory cache
                pass

    # Fallback: in-session memory cache
    key = ctx.session_id
    if key not in _DOC_CACHE:
        _DOC_CACHE[key] = empty_doc()
    return _DOC_CACHE[key]


def _save_lumendoc(ctx: ToolContext, doc: dict[str, Any]) -> None:
    """Store the document, persisting to project file or memory cache.

    - If ctx.project: write to <project_dir>/lumenframe.json
    - Else: store in memory cache (_DOC_CACHE)

    This keeps lumenframe editing fully orthogonal to the main project state
    and timeline patch history.
    """
    if ctx.project is not None:
        file_path = _lumenframe_file_path(ctx)
        if file_path is not None:
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(
                    json.dumps(copy.deepcopy(doc), ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                return
            except Exception:
                # If file-based save fails, fall back to memory cache
                pass

    # Fallback: in-session memory cache
    _DOC_CACHE[ctx.session_id] = copy.deepcopy(doc)


def _compact_tree_summary(layer: dict[str, Any], depth: int = 0, max_depth: int = 4) -> str:
    """Recursively build a compact summary of a layer tree."""
    if depth > max_depth:
        return ""

    indent = "  " * depth
    layer_id = str(layer.get("id", "?"))[:12]
    layer_type = str(layer.get("type", "?"))
    layer_name = str(layer.get("name", ""))
    visible = "👁" if layer.get("visible", True) else "🚫"
    locked = "🔒" if layer.get("locked", False) else ""

    line = f"{indent}{visible} {layer_type:12} {layer_id:12} {layer_name} {locked}"
    parts = [line]

    for child in layer.get("children") or []:
        parts.append(_compact_tree_summary(child, depth + 1, max_depth))

    return "\n".join(p for p in parts if p)


async def dispatch_get(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Inspect the session's lumenframe document state.

    Returns: compact summary of layers, selection, canvas.
    """
    if apply_layer_patch is None:
        raise RuntimeError("lumenframe module not available")

    doc = _lumendoc(ctx)
    root = doc.get("root", {})
    canvas = doc.get("canvas", {})
    selection = doc.get("selection", [])

    root_tree = _compact_tree_summary(root) if root else "(empty composition)"

    return {
        "applied": True,
        "canvas": {
            "width": canvas.get("width"),
            "height": canvas.get("height"),
            "fps": canvas.get("fps"),
        },
        "root_layers": root_tree,
        "selection_ids": selection,
        "doc_id": doc.get("id"),
    }


async def dispatch_patch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Apply a raw LayerPatch (one or more ops) atomically.

    This is the low-level verb; agents can call it directly or use
    convenience verbs (add_layer, set_transform, etc.) which wrap it.

    Args:
        ops: list of LayerPatch ops (e.g. [{"op": "add_layer", ...}, ...])

    Returns:
        applied: True if successful
        error: error message if failed
        doc_summary: compact tree after patch
    """
    if apply_layer_patch is None:
        raise RuntimeError("lumenframe module not available")

    ops = args.get("ops")
    if not ops:
        raise ValueError("lumen_patch: missing required argument 'ops'")

    if not isinstance(ops, list):
        raise ValueError(f"lumen_patch: ops must be a list, got {type(ops).__name__}")

    doc = _lumendoc(ctx)

    try:
        patch = {"version": 1, "ops": ops}
        new_doc = apply_layer_patch(doc, patch)
        _save_lumendoc(ctx, new_doc)

        root = new_doc.get("root", {})
        root_tree = _compact_tree_summary(root) if root else "(empty)"

        return {
            "applied": True,
            "ops_count": len(ops),
            "root_layers": root_tree,
            "selection_ids": new_doc.get("selection", []),
        }
    except Exception as e:
        # Apply failed; doc unchanged. Return structured error.
        error_code = getattr(e, "code", "E_UNKNOWN")
        error_msg = str(getattr(e, "message", str(e)))
        return {
            "applied": False,
            "error_code": error_code,
            "error_message": error_msg,
            "recovery": "fix_args" if error_code.startswith("E_ARG") else "none",
        }


async def dispatch_add_layer(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: create a new layer.

    Args:
        type: layer type (video, image, text, shape, audio, adjustment, solid, null, composition)
        name: optional layer name
        parent_id: optional parent layer id (default: root)
        index: optional insert position (default: end)
        at_time: optional start time on parent timeline (default: 0)

    Returns: result of dispatch_patch
    """
    layer_type = args.get("type")
    if not layer_type:
        raise ValueError("add_layer: missing required 'type'")

    op = {
        "op": "add_layer",
        "type": layer_type,
    }

    if "name" in args and args["name"]:
        op["name"] = str(args["name"])
    if "parent_id" in args and args["parent_id"]:
        op["parent_id"] = str(args["parent_id"])
    if "index" in args and args["index"] is not None:
        op["index"] = int(args["index"])
    if "at_time" in args and args["at_time"] is not None:
        op["at_time"] = float(args["at_time"])

    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_set_transform(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: transform a layer (move, scale, rotate).

    Args:
        layer_id: id of layer to transform (required)
        x: canvas x offset (pixels from centre)
        y: canvas y offset
        scale: uniform scale (overrides scale_x/scale_y)
        scale_x: horizontal scale
        scale_y: vertical scale
        rotation: rotation in degrees
        anchor_x: anchor point x (0..1)
        anchor_y: anchor point y (0..1)
    """
    layer_id = args.get("layer_id")
    if not layer_id:
        raise ValueError("set_transform: missing required 'layer_id'")

    op = {"op": "set_transform", "layer_id": str(layer_id)}

    for key in ["x", "y", "scale_x", "scale_y", "rotation", "anchor_x", "anchor_y"]:
        if key in args and args[key] is not None:
            op[key] = float(args[key])

    # Unified scale overrides scale_x/scale_y
    if "scale" in args and args["scale"] is not None:
        scale_val = float(args["scale"])
        op["scale_x"] = scale_val
        op["scale_y"] = scale_val

    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_set_opacity(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: set layer opacity.

    Args:
        layer_id: id of layer (required)
        opacity: opacity value 0..1 (required)
    """
    layer_id = args.get("layer_id")
    opacity = args.get("opacity")

    if not layer_id:
        raise ValueError("set_opacity: missing required 'layer_id'")
    if opacity is None:
        raise ValueError("set_opacity: missing required 'opacity'")

    op = {"op": "set_opacity", "layer_id": str(layer_id), "opacity": float(opacity)}
    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_delete_layer(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: delete a layer.

    Args:
        layer_id: id of layer to delete (or layer_ids for multiple)
    """
    layer_id = args.get("layer_id")
    layer_ids = args.get("layer_ids")

    if not layer_id and not layer_ids:
        raise ValueError("delete_layer: need layer_id or layer_ids")

    ids_list = [layer_id] if layer_id else list(layer_ids or [])

    ops = [{"op": "delete_layer", "layer_ids": ids_list}]
    return await dispatch_patch({"ops": ops}, ctx)


async def dispatch_move_layer(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: move a layer (reparent, reorder, retime, relane).

    Args:
        layer_id: id of layer to move (required)
        parent_id: new parent (optional)
        index: new z-order within parent (optional)
        lane: new lane hint (optional)
        start: new start time (optional)
    """
    layer_id = args.get("layer_id")
    if not layer_id:
        raise ValueError("move_layer: missing required 'layer_id'")

    op = {"op": "move_layer", "layer_id": str(layer_id)}

    if "parent_id" in args and args["parent_id"]:
        op["parent_id"] = str(args["parent_id"])
    if "index" in args and args["index"] is not None:
        op["index"] = int(args["index"])
    if "lane" in args and args["lane"] is not None:
        op["lane"] = str(args["lane"])
    if "start" in args and args["start"] is not None:
        op["start"] = float(args["start"])

    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_set_visibility(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: show/hide a layer.

    Args:
        layer_id: id of layer (required)
        visible: True to show, False to hide (required)
    """
    layer_id = args.get("layer_id")
    visible = args.get("visible")

    if not layer_id:
        raise ValueError("set_visibility: missing required 'layer_id'")
    if visible is None:
        raise ValueError("set_visibility: missing required 'visible'")

    op = {"op": "set_visibility", "layer_id": str(layer_id), "visible": bool(visible)}
    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_select(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Convenience verb: change selection.

    Args:
        layer_ids: list of ids to select (required)
        mode: 'replace' (default), 'add', 'toggle', 'clear'
    """
    layer_ids = args.get("layer_ids")
    mode = args.get("mode", "replace")

    if not layer_ids and mode != "clear":
        raise ValueError("select: need layer_ids (or mode='clear')")

    op = {"op": "select", "layer_ids": list(layer_ids or []), "mode": mode}
    return await dispatch_patch({"ops": [op]}, ctx)


async def dispatch_render(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Render the current lumenframe document to a video or image file.

    Compiles the lumenframe doc and renders it to MP4 (default) or PNG frame.
    Missing media assets degrade gracefully (skipped).

    Args:
        format: "video" (default, MP4) or "frame" (single PNG at specified index)
        frame_index: For format="frame", the frame number to render (default 0)

    Returns:
        Dict with asset_id, path, dimensions, duration (for video), etc.
    """
    if apply_layer_patch is None or compile_to_layer_stack is None:
        return {
            "applied": False,
            "error_code": "E_NOT_AVAILABLE",
            "error_message": "lumenframe compile module not available",
        }

    doc = _lumendoc(ctx)
    fmt = str(args.get("format", "video")).lower()
    frame_index = int(args.get("frame_index", 0))

    # Compile the document to a renderable stack
    try:
        stack = compile_to_layer_stack(doc, strict=False)
    except Exception as e:
        return {
            "applied": False,
            "error_code": "E_COMPILE",
            "error_message": f"Compile failed: {str(e)}",
        }

    # Determine output format and path
    if fmt == "frame":
        output_kind = "image"
        output_ext = ".png"
    else:
        output_kind = "video"
        output_ext = ".mp4"
        fmt = "video"

    asset_id = ctx.registry.allocate_id(output_kind)
    out_path = ctx.child_path(asset_id, output_ext)

    # Render
    try:
        if fmt == "video":
            # Render full video
            out_path_str = stack.render_to_video(str(out_path))
            width, height = stack.width, stack.height
            total_frames = stack.total_frames
            duration_sec = total_frames / stack.fps

            summary = f"lumenframe render ({width}×{height} @ {stack.fps} fps, {total_frames} frames)"
            ctx.registry.register_output(
                asset_id,
                kind=output_kind,
                path=Path(out_path_str),
                summary=summary,
            )

            return {
                "applied": True,
                "asset_id": asset_id,
                "path": str(out_path),
                "width": width,
                "height": height,
                "fps": stack.fps,
                "total_frames": total_frames,
                "duration_sec": duration_sec,
                "format": "mp4",
                "summary": summary,
            }
        else:  # frame
            # Render single frame
            frame_index = min(max(frame_index, 0), stack.total_frames - 1)
            frame_rgba = stack.render_frame(frame_index)

            # Convert to PIL Image and save as PNG
            import numpy as np
            from PIL import Image as PILImage

            # Ensure float32 [0, 1]
            frame_uint8 = np.asarray(frame_rgba * 255, dtype=np.uint8)
            img = PILImage.fromarray(frame_uint8, "RGBA")
            img.save(str(out_path))

            width, height = stack.width, stack.height
            summary = f"lumenframe frame render (frame {frame_index} of {stack.total_frames})"
            ctx.registry.register_output(
                asset_id,
                kind=output_kind,
                path=out_path,
                summary=summary,
            )

            return {
                "applied": True,
                "asset_id": asset_id,
                "path": str(out_path),
                "width": width,
                "height": height,
                "frame_index": frame_index,
                "total_frames": stack.total_frames,
                "format": "png",
                "summary": summary,
            }
    except Exception as e:
        return {
            "applied": False,
            "error_code": "E_RENDER",
            "error_message": f"Render failed: {str(e)}",
        }


def clear_lumenframe_session(session_id: str) -> None:
    """Clear the lumenframe document cache for a session.

    Call this when a session ends to clean up memory. Can be integrated into
    agent_loop_v3's session teardown if/when one is added.

    Args:
        session_id: The session identifier to clear from _DOC_CACHE.
    """
    if session_id in _DOC_CACHE:
        del _DOC_CACHE[session_id]


# Dispatchers table (called by agent_loop_v3.py via DISPATCHER)
dispatch = dispatch_patch  # The main entry point
dispatch_get_lumenframe = dispatch_get
dispatch_lumen_patch = dispatch_patch
dispatch_lumen_add_layer = dispatch_add_layer
dispatch_lumen_set_transform = dispatch_set_transform
dispatch_lumen_set_opacity = dispatch_set_opacity
dispatch_lumen_delete_layer = dispatch_delete_layer
dispatch_lumen_move_layer = dispatch_move_layer
dispatch_lumen_set_visibility = dispatch_set_visibility
dispatch_lumen_select = dispatch_select
dispatch_lumen_render = dispatch_render


__all__ = [
    "dispatch",
    "dispatch_get",
    "dispatch_patch",
    "dispatch_add_layer",
    "dispatch_set_transform",
    "dispatch_set_opacity",
    "dispatch_delete_layer",
    "dispatch_move_layer",
    "dispatch_set_visibility",
    "dispatch_select",
    "dispatch_render",
    "clear_lumenframe_session",
]
