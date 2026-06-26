"""lumen_*: lumenframe layer editing verbs.

This module exposes the lumenframe LayerPatch vocabulary as agent verbs
(similar to timeline_* verbs). The document state is held in session
context to enable atomic edits and state inspection.

Design contract:
- Each verb compiles to a LayerPatch and applies it atomically via
  ``apply_layer_patch``.
- ``get_lumenframe`` returns a compact summary of the current doc state
  (layer tree, selection).
- ``lumen_patch`` is the low-level verb that accepts a raw ops list;
  convenience verbs (add_layer, set_transform, etc.) are built on top.
- Doc persistence: we use in-session memory + minimal fallback. If a project
  handle is available (ctx.project), we can later extend to persistent
  storage.
"""
from __future__ import annotations

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
    )
except ImportError:
    # Graceful fallback if lumenframe is not available
    empty_doc = None
    apply_layer_patch = None
    describe_ops = None
    new_layer = None
    find_layer = None
    find_parent = None


# Session-local doc state cache (keyed by session_id)
_DOC_CACHE: dict[str, dict[str, Any]] = {}


def _lumendoc(ctx: ToolContext) -> dict[str, Any]:
    """Retrieve or initialize the session's lumenframe document.

    Strategy: in-session memory first. If a persistent project handle
    becomes available in the future, we can extend this to also load/save
    from disk.
    """
    if empty_doc is None:
        raise RuntimeError("lumenframe module not available")

    key = ctx.session_id
    if key not in _DOC_CACHE:
        _DOC_CACHE[key] = empty_doc()
    return _DOC_CACHE[key]


def _save_lumendoc(ctx: ToolContext, doc: dict[str, Any]) -> None:
    """Store the document back to session cache."""
    _DOC_CACHE[ctx.session_id] = doc


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
]
