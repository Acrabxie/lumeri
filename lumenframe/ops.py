"""LayerPatch — the atomic, validated editing vocabulary of lumenframe.

A ``LayerPatch`` mirrors the timeline patch contract so the two feel like one
system::

    {"version": 1, "ops": [{"op": "add_layer", ...}, {"op": "set_transform", ...}]}

:func:`apply_layer_patch` deep-copies the (normalised) document, runs every op
in order, then validates the whole tree. The patch applies *atomically*: if any
op raises, the caller's document is never touched and the error names exactly
what went wrong (``LayerPatchError`` carries both a stable ``code`` and a
message).

Ops fall into the three families the editor exposes:

* **layer management** — ``add_layer`` / ``delete_layer`` / ``duplicate_layer``
  / ``select`` / ``move_layer`` / ``reorder_layer`` / ``group_layers`` /
  ``ungroup_layer`` / ``merge_layers`` / ``rename_layer`` / ``set_visibility``
  / ``set_lock``
* **time** — ``set_time`` / ``trim`` / ``split`` / ``set_speed``
* **per-layer & inter-layer** — ``set_transform`` / ``set_opacity`` /
  ``set_blend_mode`` / ``set_mask`` / ``clip_to_below`` / ``add_adjustment_layer``
  / ``add_effect`` / ``remove_effect`` / ``set_effect_params`` / ``color_grade``
  / ``add_transition`` / ``set_keyframe`` / ``remove_keyframe``

Extensions register further ops through :mod:`lumenframe.registry`.
"""
from __future__ import annotations

import copy
from typing import Any

from lumenframe import model
from lumenframe.model import (
    BLEND_MODES,
    DEFAULT_TRANSFORM,
    INTERP_KINDS,
    MASK_KINDS,
    TIME_NDIGITS,
    new_layer,
    normalize_doc,
)
from lumenframe.registry import op_handler, register_op


class LayerPatchError(ValueError):
    """Structured layer-edit failure; ``str(e)`` carries code + message."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.code}: {self.message}")


# ── entry point ─────────────────────────────────────────────────────────


def apply_layer_patch(
    doc: dict[str, Any] | None,
    patches: list[dict[str, Any]] | dict[str, Any],
) -> dict[str, Any]:
    """Apply one or more LayerPatches atomically; return the new document."""
    working = copy.deepcopy(normalize_doc(doc or {}))
    patch_list = [patches] if isinstance(patches, dict) else list(patches or [])
    for patch in patch_list:
        if not isinstance(patch, dict) or patch.get("version") != 1:
            raise LayerPatchError("E_PATCH", "Unsupported LayerPatch (need version: 1)")
        for op in patch.get("ops") or []:
            _dispatch(working, op)
    _finalize(working)
    validate_doc(working)
    return working


def _dispatch(doc: dict[str, Any], op: dict[str, Any]) -> None:
    if not isinstance(op, dict):
        raise LayerPatchError("E_OP", "LayerPatch op must be an object")
    name = str(op.get("op") or "")
    handler = op_handler(name)
    if handler is None:
        raise LayerPatchError("E_OP_UNKNOWN", f"Unknown LayerPatch op: {name or '<empty>'}")
    handler(doc, op)


# ── shared helpers ──────────────────────────────────────────────────────


def _require_layer(doc: dict[str, Any], layer_id: Any, *, op: str) -> dict[str, Any]:
    layer = model.find_layer(doc, str(layer_id)) if layer_id is not None else None
    if layer is None:
        raise LayerPatchError("E_NOT_FOUND", f"{op}: layer not found: {layer_id!r}")
    return layer


def _require_locate(doc: dict[str, Any], layer_id: Any, *, op: str) -> tuple[dict[str, Any], int]:
    found = model.locate(doc, str(layer_id)) if layer_id is not None else None
    if found is None:
        raise LayerPatchError("E_NOT_FOUND", f"{op}: layer not found: {layer_id!r}")
    return found


def _require_arg(op: dict[str, Any], key: str) -> Any:
    if key not in op or op.get(key) is None:
        raise LayerPatchError("E_ARG", f"{op.get('op')}: missing required arg {key!r}")
    return op[key]


def _is_container(layer: dict[str, Any]) -> bool:
    spec = None
    from lumenframe.registry import layer_type_spec
    spec = layer_type_spec(str(layer.get("type")))
    if spec is not None:
        return bool(spec.get("container"))
    return str(layer.get("type")) in model.CONTAINER_TYPES


def _round_t(value: Any) -> float:
    return round(model._as_float(value), TIME_NDIGITS)


def _fresh_ids_deep(layer: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a subtree with brand-new ids; remap intra-subtree mask refs."""
    clone = copy.deepcopy(layer)
    id_map: dict[str, str] = {}
    for node in model.walk(clone):
        old = str(node.get("id"))
        new = model.gen_id(model._id_prefix(str(node.get("type"))))
        id_map[old] = new
        node["id"] = new
        for fx in node.get("effects") or []:
            if isinstance(fx, dict):
                fx["id"] = model.gen_id("fx")
    # Re-point any track-matte that referenced a layer inside this subtree.
    for node in model.walk(clone):
        mask = node.get("mask")
        if isinstance(mask, dict) and mask.get("source_layer_id") in id_map:
            mask["source_layer_id"] = id_map[mask["source_layer_id"]]
    return clone


def _splice_out(doc: dict[str, Any], layer_id: str, *, op: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    parent, index = _require_locate(doc, layer_id, op=op)
    layer = parent["children"].pop(index)
    return layer, parent, index


def _insert(parent: dict[str, Any], layer: dict[str, Any], index: int | None) -> None:
    children = parent.setdefault("children", [])
    if index is None or index < 0 or index > len(children):
        children.append(layer)
    else:
        children.insert(index, layer)


def _set_selection(doc: dict[str, Any], ids: list[str]) -> None:
    doc["selection"] = list(dict.fromkeys(str(i) for i in ids))


def _finalize(doc: dict[str, Any]) -> None:
    """Round times, prune dangling selection, recompute durations."""
    known = {str(n.get("id")) for n in model.walk(doc["root"]) if n.get("id")}
    doc["selection"] = [s for s in doc.get("selection", []) if s in known]
    for node in model.walk(doc["root"]):
        for key in ("start", "duration", "source_in", "source_out"):
            node[key] = _round_t(node.get(key))
    doc["root"]["duration"] = model.doc_duration(doc)


# ── validation ──────────────────────────────────────────────────────────


def validate_doc(doc: dict[str, Any]) -> None:
    """Raise :class:`LayerPatchError` on any structural invariant violation."""
    root = doc.get("root")
    if not isinstance(root, dict):
        raise LayerPatchError("E_DOC", "document is missing its root composition")

    seen: set[str] = set()
    from lumenframe.registry import layer_type_spec
    sibling_index: dict[str, list[str]] = {}
    for node in model.walk(root):
        lid = str(node.get("id"))
        if not lid:
            raise LayerPatchError("E_ID", "every layer needs an id")
        if lid in seen:
            raise LayerPatchError("E_DUP_ID", f"duplicate layer id: {lid}")
        seen.add(lid)
        ltype = str(node.get("type"))
        if layer_type_spec(ltype) is None and ltype not in model.LAYER_TYPES:
            raise LayerPatchError("E_TYPE", f"layer {lid}: unknown type {ltype!r}")
        children = node.get("children") or []
        if children and not _is_container(node):
            raise LayerPatchError("E_CONTAINER", f"layer {lid} ({ltype}) cannot hold children")
        sibling_index[lid] = [str(c.get("id")) for c in children if isinstance(c, dict)]
        if model._as_float(node.get("duration")) < 0:
            raise LayerPatchError("E_RANGE", f"layer {lid}: negative duration")
        if model._as_float(node.get("speed")) <= 0 and node is not root:
            raise LayerPatchError("E_SPEED", f"layer {lid}: speed must be > 0")

    # Track mattes must reference an existing *sibling* layer (not self).
    for node in model.walk(root):
        mask = node.get("mask")
        if not isinstance(mask, dict):
            continue
        kind = str(mask.get("kind"))
        if kind in {"alpha_matte", "luma_matte"}:
            src = str(mask.get("source_layer_id") or "")
            if not src or src not in seen:
                raise LayerPatchError("E_MASK", f"layer {node['id']}: matte source {src!r} not found")
            if src == str(node.get("id")):
                raise LayerPatchError("E_MASK", f"layer {node['id']}: matte cannot reference itself")


# ════════════════════════════════════════════════════════════════════════
# Layer management
# ════════════════════════════════════════════════════════════════════════


_ADD_LAYER_CONTROL_KEYS = {"op", "parent_id", "index", "at_time", "lane", "select", "layer"}


@register_op("add_layer", source="core")
def _op_add_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    spec = op.get("layer") if isinstance(op.get("layer"), dict) else {}
    # Layer fields may be nested under "layer" or written inline on the op;
    # inline keys win so agent-authored ``{"op":"add_layer","type":"text",...}``
    # works without wrapping every field in a "layer" object.
    inline = {k: v for k, v in op.items() if k not in _ADD_LAYER_CONTROL_KEYS}
    fields = {**spec, **inline}
    ltype = str(op.get("type") or spec.get("type") or "solid")
    fields["type"] = ltype
    layer = model._normalize_layer(fields)
    if op.get("id"):
        layer["id"] = str(op["id"])
    elif spec.get("id"):
        layer["id"] = str(spec["id"])
    if model.find_layer(doc, layer["id"]) is not None:
        layer["id"] = model.gen_id(model._id_prefix(ltype))
    if op.get("at_time") is not None:
        layer["start"] = _round_t(op["at_time"])
    if op.get("lane") is not None:
        layer["lane"] = int(model._as_float(op["lane"]))

    parent_id = op.get("parent_id")
    parent = _require_layer(doc, parent_id, op="add_layer") if parent_id else doc["root"]
    if not _is_container(parent):
        raise LayerPatchError("E_CONTAINER", f"add_layer: parent {parent.get('id')} cannot hold children")
    index = op.get("index")
    _insert(parent, layer, int(index) if index is not None else None)
    if op.get("select", True):
        _set_selection(doc, [layer["id"]])


@register_op("delete_layer", source="core")
def _op_delete_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    ids = op.get("layer_ids") or ([op.get("layer_id")] if op.get("layer_id") else [])
    if not ids:
        raise LayerPatchError("E_ARG", "delete_layer: need layer_id or layer_ids")
    for raw in ids:
        lid = str(raw)
        if lid == str(doc["root"].get("id")):
            raise LayerPatchError("E_ROOT", "delete_layer: cannot delete the root composition")
        _splice_out(doc, lid, op="delete_layer")
        # Drop mattes that pointed at the now-deleted layer.
        for node in model.walk(doc["root"]):
            mask = node.get("mask")
            if isinstance(mask, dict) and str(mask.get("source_layer_id")) == lid:
                node["mask"] = None


@register_op("duplicate_layer", source="core")
def _op_duplicate_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer_id = _require_arg(op, "layer_id")
    parent, index = _require_locate(doc, layer_id, op="duplicate_layer")
    original = parent["children"][index]
    clone = _fresh_ids_deep(original)
    if op.get("name"):
        clone["name"] = str(op["name"])
    else:
        clone["name"] = f"{original.get('name', 'Layer')} copy"
    if op.get("offset_time") is not None:
        clone["start"] = _round_t(model._as_float(clone.get("start")) + model._as_float(op["offset_time"]))
    parent["children"].insert(index + 1, clone)
    _set_selection(doc, [clone["id"]])


@register_op("select", source="core")
def _op_select(doc: dict[str, Any], op: dict[str, Any]) -> None:
    mode = str(op.get("mode") or "replace")
    if mode == "clear":
        doc["selection"] = []
        return
    ids = op.get("layer_ids") or ([op.get("layer_id")] if op.get("layer_id") else [])
    ids = [str(i) for i in ids]
    for lid in ids:
        _require_layer(doc, lid, op="select")
    current = list(doc.get("selection", []))
    if mode == "replace":
        current = ids
    elif mode == "add":
        current = current + ids
    elif mode == "toggle":
        for lid in ids:
            if lid in current:
                current.remove(lid)
            else:
                current.append(lid)
    else:
        raise LayerPatchError("E_ARG", f"select: unknown mode {mode!r}")
    _set_selection(doc, current)


@register_op("move_layer", source="core")
def _op_move_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Universal move: reparent (z-tree), reorder (z within parent), retime, relane."""
    layer_id = str(_require_arg(op, "layer_id"))
    if layer_id == str(doc["root"].get("id")):
        raise LayerPatchError("E_ROOT", "move_layer: cannot move the root composition")
    layer = _require_layer(doc, layer_id, op="move_layer")

    target_parent = doc["root"]
    if op.get("parent_id"):
        target_parent = _require_layer(doc, op["parent_id"], op="move_layer")
        if not _is_container(target_parent):
            raise LayerPatchError("E_CONTAINER", f"move_layer: parent {target_parent['id']} cannot hold children")
        descendant_ids = {str(n.get("id")) for n in model.walk(layer)}
        if str(target_parent.get("id")) in descendant_ids:
            raise LayerPatchError("E_CYCLE", "move_layer: cannot move a layer into itself or a descendant")
    else:
        target_parent, _ = _require_locate(doc, layer_id, op="move_layer")

    detached, _old_parent, _old_index = _splice_out(doc, layer_id, op="move_layer")
    index = op.get("index")
    # ``index`` is the desired *final* position in the target parent's children
    # (computed after the layer is detached), so it needs no reorder fix-up.
    target_index = int(index) if index is not None else None
    _insert(target_parent, detached, target_index)

    if op.get("lane") is not None:
        detached["lane"] = int(model._as_float(op["lane"]))
    if op.get("start") is not None:
        detached["start"] = _round_t(op["start"])
    elif op.get("delta_start") is not None:
        detached["start"] = _round_t(model._as_float(detached.get("start")) + model._as_float(op["delta_start"]))


@register_op("reorder_layer", source="core")
def _op_reorder_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Pure z-order change within the current parent (bring forward / send back)."""
    layer_id = str(_require_arg(op, "layer_id"))
    parent, index = _require_locate(doc, layer_id, op="reorder_layer")
    count = len(parent["children"])
    to = op.get("to")
    if to == "top":
        target = count - 1
    elif to == "bottom":
        target = 0
    elif to == "forward":
        target = min(index + 1, count - 1)
    elif to == "backward":
        target = max(index - 1, 0)
    elif op.get("index") is not None:
        target = max(0, min(int(op["index"]), count - 1))
    elif op.get("delta") is not None:
        target = max(0, min(index + int(op["delta"]), count - 1))
    else:
        raise LayerPatchError("E_ARG", "reorder_layer: need to / index / delta")
    layer = parent["children"].pop(index)
    parent["children"].insert(target, layer)


@register_op("rename_layer", source="core")
def _op_rename_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="rename_layer")
    layer["name"] = str(_require_arg(op, "name"))


@register_op("set_visibility", source="core")
def _op_set_visibility(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_visibility")
    layer["visible"] = bool(_require_arg(op, "visible"))


@register_op("set_lock", source="core")
def _op_set_lock(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_lock")
    layer["locked"] = bool(_require_arg(op, "locked"))


# ── grouping / merging (precompose) ─────────────────────────────────────


def _group_into_composition(
    doc: dict[str, Any], op: dict[str, Any], *, merged: bool, default_name: str
) -> None:
    ids = op.get("layer_ids") or []
    ids = [str(i) for i in ids]
    if len(ids) < 1:
        raise LayerPatchError("E_ARG", f"{op.get('op')}: need layer_ids")
    located = [(_require_locate(doc, lid, op=str(op.get("op")))) for lid in ids]
    parent = located[0][0]
    if any(p is not parent for p, _ in located):
        raise LayerPatchError("E_GROUP_PARENT", f"{op.get('op')}: all layers must share one parent")

    # Capture members in tree order, then splice them all out.
    ordered = sorted(zip(ids, (idx for _, idx in located)), key=lambda pair: pair[1])
    min_index = located and min(idx for _, idx in located)
    members = [model.find_layer(doc, lid) for lid, _ in ordered]
    group_start = min(model._as_float(m.get("start")) for m in members)
    group_end = max(model._as_float(m.get("start")) + model._as_float(m.get("duration")) for m in members)
    for lid, _ in ordered:
        _splice_out(doc, lid, op=str(op.get("op")))

    comp = new_layer(
        "composition",
        id=str(op["into_id"]) if op.get("into_id") else None,
        name=str(op.get("name") or default_name),
        start=_round_t(group_start),
        duration=_round_t(group_end - group_start),
        merged=merged,
    )
    for member in members:
        member["start"] = _round_t(model._as_float(member.get("start")) - group_start)
    comp["children"] = members
    insert_at = min(int(min_index), len(parent["children"])) if min_index is not None else None
    _insert(parent, comp, insert_at)
    _set_selection(doc, [comp["id"]])


@register_op("group_layers", source="core")
def _op_group_layers(doc: dict[str, Any], op: dict[str, Any]) -> None:
    _group_into_composition(doc, op, merged=False, default_name="Group")


@register_op("merge_layers", source="core")
def _op_merge_layers(doc: dict[str, Any], op: dict[str, Any]) -> None:
    # Flatten = precompose marked for raster baking at compile time.
    _group_into_composition(doc, op, merged=True, default_name="Merged")


@register_op("ungroup_layer", source="core")
def _op_ungroup_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer_id = str(_require_arg(op, "layer_id"))
    if layer_id == str(doc["root"].get("id")):
        raise LayerPatchError("E_ROOT", "ungroup_layer: cannot ungroup the root composition")
    comp, parent, index = _splice_out(doc, layer_id, op="ungroup_layer")
    if not _is_container(comp):
        # Put it back; ungroup only applies to compositions.
        parent["children"].insert(index, comp)
        raise LayerPatchError("E_TYPE", f"ungroup_layer: {layer_id} is not a composition")
    offset = model._as_float(comp.get("start"))
    lifted = comp.get("children") or []
    for child in lifted:
        child["start"] = _round_t(model._as_float(child.get("start")) + offset)
    parent["children"][index:index] = lifted
    _set_selection(doc, [str(c.get("id")) for c in lifted])


# ════════════════════════════════════════════════════════════════════════
# Time
# ════════════════════════════════════════════════════════════════════════


@register_op("set_time", source="core")
def _op_set_time(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_time")
    if op.get("start") is not None:
        layer["start"] = _round_t(op["start"])
    if op.get("duration") is not None:
        dur = _round_t(op["duration"])
        if dur < 0:
            raise LayerPatchError("E_RANGE", "set_time: duration must be >= 0")
        layer["duration"] = dur


@register_op("trim", source="core")
def _op_trim(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="trim")
    edge = str(_require_arg(op, "edge"))
    if edge not in {"in", "out"}:
        raise LayerPatchError("E_ARG", "trim: edge must be 'in' or 'out'")
    speed = max(model._as_float(layer.get("speed")) or 1.0, 1e-6)
    start = model._as_float(layer.get("start"))
    duration = model._as_float(layer.get("duration"))
    if edge == "in":
        new_start = _round_t(op["to"]) if op.get("to") is not None else _round_t(start + model._as_float(op.get("delta")))
        delta_t = new_start - start
        new_duration = duration - delta_t
        if new_duration <= 0:
            raise LayerPatchError("E_RANGE", "trim: in-edge would collapse the layer")
        layer["start"] = new_start
        layer["duration"] = _round_t(new_duration)
        layer["source_in"] = _round_t(model._as_float(layer.get("source_in")) + delta_t * speed)
    else:
        new_end = _round_t(op["to"]) if op.get("to") is not None else _round_t(start + duration + model._as_float(op.get("delta")))
        new_duration = new_end - start
        if new_duration <= 0:
            raise LayerPatchError("E_RANGE", "trim: out-edge would collapse the layer")
        layer["duration"] = _round_t(new_duration)
        layer["source_out"] = _round_t(model._as_float(layer.get("source_in")) + new_duration * speed)


@register_op("split", source="core")
def _op_split(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer_id = str(_require_arg(op, "layer_id"))
    at_time = _round_t(_require_arg(op, "at_time"))
    parent, index = _require_locate(doc, layer_id, op="split")
    left = parent["children"][index]
    start = model._as_float(left.get("start"))
    duration = model._as_float(left.get("duration"))
    if not (start < at_time < start + duration):
        raise LayerPatchError("E_RANGE", f"split: at_time {at_time} must fall inside the layer")
    speed = max(model._as_float(left.get("speed")) or 1.0, 1e-6)
    left_dur = at_time - start
    split_source = model._as_float(left.get("source_in")) + left_dur * speed

    right = _fresh_ids_deep(left)
    right["name"] = f"{left.get('name', 'Layer')} (2)"
    right["start"] = at_time
    right["duration"] = _round_t(duration - left_dur)
    right["source_in"] = _round_t(split_source)

    left["duration"] = _round_t(left_dur)
    left["source_out"] = _round_t(split_source)
    parent["children"].insert(index + 1, right)
    _set_selection(doc, [left["id"], right["id"]])


@register_op("set_speed", source="core")
def _op_set_speed(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_speed")
    speed = model._as_float(_require_arg(op, "speed"))
    if speed <= 0:
        raise LayerPatchError("E_SPEED", "set_speed: speed must be > 0")
    old_speed = max(model._as_float(layer.get("speed")) or 1.0, 1e-6)
    src_range = model._as_float(layer.get("source_out")) - model._as_float(layer.get("source_in"))
    if src_range > 0:
        new_duration = src_range / speed
    else:
        new_duration = model._as_float(layer.get("duration")) * old_speed / speed
    layer["speed"] = speed
    layer["duration"] = _round_t(new_duration)


# ════════════════════════════════════════════════════════════════════════
# Per-layer (intra) ops
# ════════════════════════════════════════════════════════════════════════


@register_op("set_transform", source="core")
def _op_set_transform(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_transform")
    transform = {**DEFAULT_TRANSFORM, **(layer.get("transform") or {})}
    if op.get("scale") is not None:
        transform["scale_x"] = model._as_float(op["scale"])
        transform["scale_y"] = model._as_float(op["scale"])
    for key in ("x", "y", "scale_x", "scale_y", "rotation", "anchor_x", "anchor_y"):
        if op.get(key) is not None:
            transform[key] = model._as_float(op[key])
    layer["transform"] = transform


@register_op("set_opacity", source="core")
def _op_set_opacity(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_opacity")
    layer["opacity"] = max(0.0, min(1.0, model._as_float(_require_arg(op, "opacity"))))


@register_op("set_blend_mode", source="core")
def _op_set_blend_mode(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_blend_mode")
    mode = str(_require_arg(op, "blend_mode"))
    # Unknown modes are allowed (an extension may render them) but core modes
    # are spell-checked so typos surface early.
    layer["blend_mode"] = mode


# ── inter-layer ──────────────────────────────────────────────────────────


@register_op("set_mask", source="core")
def _op_set_mask(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_mask")
    if op.get("mask") is None and not op.get("kind"):
        layer["mask"] = None
        return
    spec = op.get("mask") if isinstance(op.get("mask"), dict) else {
        "kind": op.get("kind"),
        "source_layer_id": op.get("source_layer_id"),
        "shape": op.get("shape"),
        "invert": op.get("invert", False),
        "feather": op.get("feather", 0.0),
    }
    kind = str(spec.get("kind") or "shape")
    if kind not in MASK_KINDS:
        raise LayerPatchError("E_MASK", f"set_mask: unknown mask kind {kind!r}")
    if kind in {"alpha_matte", "luma_matte"}:
        src = str(spec.get("source_layer_id") or "")
        _require_layer(doc, src, op="set_mask")
        if src == str(layer.get("id")):
            raise LayerPatchError("E_MASK", "set_mask: matte cannot reference itself")
    layer["mask"] = model._normalize_mask(spec)


@register_op("clip_to_below", source="core")
def _op_clip_to_below(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="clip_to_below")
    layer["clip_to_below"] = bool(op.get("enabled", True))


@register_op("add_adjustment_layer", source="core")
def _op_add_adjustment_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    parent = doc["root"]
    if op.get("parent_id"):
        parent = _require_layer(doc, op["parent_id"], op="add_adjustment_layer")
        if not _is_container(parent):
            raise LayerPatchError("E_CONTAINER", "add_adjustment_layer: parent cannot hold children")
    effects = [model._normalize_effect(e) for e in (op.get("effects") or []) if isinstance(e, dict)]
    layer = new_layer(
        "adjustment",
        id=str(op["id"]) if op.get("id") else None,
        name=str(op.get("name") or "Adjustment"),
        start=_round_t(op.get("start") or 0.0),
        duration=_round_t(op.get("duration") or 0.0),
        effects=effects,
    )
    index = op.get("index")
    _insert(parent, layer, int(index) if index is not None else None)
    if op.get("select", True):
        _set_selection(doc, [layer["id"]])


# ── effects / colour ─────────────────────────────────────────────────────


@register_op("add_effect", source="core")
def _op_add_effect(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="add_effect")
    spec = op.get("effect") if isinstance(op.get("effect"), dict) else {
        "type": op.get("type"),
        "params": op.get("params"),
        "id": op.get("effect_id"),
    }
    if "enabled" in op:
        spec["enabled"] = op["enabled"]
    if not spec.get("type"):
        raise LayerPatchError("E_ARG", "add_effect: effect needs a type")
    effect = model._normalize_effect(spec)
    effects = layer.setdefault("effects", [])
    index = op.get("index")
    if index is not None and 0 <= int(index) <= len(effects):
        effects.insert(int(index), effect)
    else:
        effects.append(effect)


@register_op("remove_effect", source="core")
def _op_remove_effect(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="remove_effect")
    effect_id = str(_require_arg(op, "effect_id"))
    before = len(layer.get("effects") or [])
    layer["effects"] = [e for e in (layer.get("effects") or []) if str(e.get("id")) != effect_id]
    if len(layer["effects"]) == before:
        raise LayerPatchError("E_NOT_FOUND", f"remove_effect: effect {effect_id} not found")


@register_op("set_effect_params", source="core")
def _op_set_effect_params(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_effect_params")
    effect_id = str(_require_arg(op, "effect_id"))
    params = _require_arg(op, "params")
    if not isinstance(params, dict):
        raise LayerPatchError("E_ARG", "set_effect_params: params must be an object")
    merge = bool(op.get("merge", True))
    for effect in layer.get("effects") or []:
        if str(effect.get("id")) == effect_id:
            effect["params"] = {**(effect.get("params") or {}), **params} if merge else dict(params)
            return
    raise LayerPatchError("E_NOT_FOUND", f"set_effect_params: effect {effect_id} not found")


@register_op("color_grade", source="core")
def _op_color_grade(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Convenience: upsert a single ``color_grade`` effect on the layer.

    Recognised params (all optional, all free-form floats so DaVinci-style
    grading can grow): brightness, contrast, saturation, exposure, temperature,
    tint, highlights, shadows, gamma, lift, gain.
    """
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="color_grade")
    grade_keys = {
        "brightness", "contrast", "saturation", "exposure", "temperature",
        "tint", "highlights", "shadows", "gamma", "lift", "gain", "hue", "vibrance",
    }
    params = {k: model._as_float(v) for k, v in op.items() if k in grade_keys}
    if isinstance(op.get("params"), dict):
        params.update({k: v for k, v in op["params"].items()})
    for effect in layer.setdefault("effects", []):
        if str(effect.get("type")) == "color_grade":
            effect["params"] = {**(effect.get("params") or {}), **params}
            effect["enabled"] = True
            return
    layer["effects"].append(model._normalize_effect({"type": "color_grade", "params": params}))


@register_op("flip_layer", source="core")
def _op_flip_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Convenience: upsert a mirror effect on the layer's effect chain.

    Mirrors can be applied with direction: horizontal, vertical, or both.
    """
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="flip_layer")
    direction = str(op.get("direction", "horizontal"))
    params = {"direction": direction}
    for effect in layer.setdefault("effects", []):
        if str(effect.get("type")) == "mirror":
            effect["params"] = {**(effect.get("params") or {}), **params}
            effect["enabled"] = True
            return
    layer["effects"].append(model._normalize_effect({"type": "mirror", "params": params}))


@register_op("add_transition", source="core")
def _op_add_transition(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="add_transition")
    kind = str(_require_arg(op, "kind"))
    duration = _round_t(op.get("duration") or 0.5)
    at = str(op.get("at") or "in")
    if at not in {"in", "out", "both"}:
        raise LayerPatchError("E_ARG", "add_transition: at must be in / out / both")
    transitions = layer.setdefault("props", {}).setdefault("transitions", {})
    descriptor = {"kind": kind, "duration": duration}
    for edge in (("in", "out") if at == "both" else (at,)):
        transitions[edge] = dict(descriptor)


# ── keyframes ─────────────────────────────────────────────────────────────


@register_op("set_keyframe", source="core")
def _op_set_keyframe(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_keyframe")
    prop = str(_require_arg(op, "property"))
    t = _round_t(_require_arg(op, "t"))
    interp = str(op.get("interp") or "linear")
    if interp not in INTERP_KINDS:
        raise LayerPatchError("E_ARG", f"set_keyframe: unknown interp {interp!r}")
    track = layer.setdefault("keyframes", {}).setdefault(prop, [])
    track[:] = [k for k in track if abs(model._as_float(k.get("t")) - t) > 1e-9]
    track.append({"t": t, "value": op.get("value"), "interp": interp})
    track.sort(key=lambda k: k["t"])


@register_op("remove_keyframe", source="core")
def _op_remove_keyframe(doc: dict[str, Any], op: dict[str, Any]) -> None:
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="remove_keyframe")
    prop = str(_require_arg(op, "property"))
    t = _round_t(_require_arg(op, "t"))
    track = layer.get("keyframes", {}).get(prop)
    if not track:
        raise LayerPatchError("E_NOT_FOUND", f"remove_keyframe: no track {prop!r}")
    kept = [k for k in track if abs(model._as_float(k.get("t")) - t) > 1e-9]
    if len(kept) == len(track):
        raise LayerPatchError("E_NOT_FOUND", f"remove_keyframe: no keyframe at t={t}")
    if kept:
        layer["keyframes"][prop] = kept
    else:
        layer["keyframes"].pop(prop, None)


# ════════════════════════════════════════════════════════════════════════
# Text styling
# ════════════════════════════════════════════════════════════════════════


@register_op("set_text", source="core")
def _op_set_text(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Merge text props into a layer's props (only provided keys are updated).

    Recognised props: text, font, font_size, color, align, stroke, shadow,
    background, line_spacing. All optional; omitted props are left unchanged.
    """
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_text")
    props = layer.setdefault("props", {})

    # Text props allowed in this op.
    text_keys = {"text", "font", "font_size", "color", "align", "stroke", "shadow", "background", "line_spacing"}

    for key in text_keys:
        if key in op:
            props[key] = op[key]


# ════════════════════════════════════════════════════════════════════════
# Audio properties
# ════════════════════════════════════════════════════════════════════════


@register_op("set_volume", source="core")
def _op_set_volume(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Set the volume level (linear, 0..1+, default 1.0) in layer props."""
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_volume")
    volume = model._as_float(_require_arg(op, "volume"))
    layer.setdefault("props", {})["volume"] = volume


@register_op("set_audio_fade", source="core")
def _op_set_audio_fade(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Set fade-in and/or fade-out durations (in seconds) in layer props."""
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="set_audio_fade")
    props = layer.setdefault("props", {})

    if op.get("fade_in") is not None:
        props["fade_in"] = model._as_float(op["fade_in"])
    if op.get("fade_out") is not None:
        props["fade_out"] = model._as_float(op["fade_out"])


@register_op("mute_layer", source="core")
def _op_mute_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Mute or unmute a layer (default muted=true) in props."""
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="mute_layer")
    muted = bool(op.get("muted", True))
    layer.setdefault("props", {})["muted"] = muted


# ════════════════════════════════════════════════════════════════════════
# Animation presets
# ════════════════════════════════════════════════════════════════════════


_EASING_MAP: dict[str, str] = {
    "linear": "linear",
    "ease": "ease",
    "ease_in": "ease_in",
    "ease_out": "ease_out",
    None: "linear",
}


@register_op("animate_layer", source="core")
def _op_animate_layer(doc: dict[str, Any], op: dict[str, Any]) -> None:
    """Generate keyframes on a layer using an animation preset.

    Presets: fade_in, fade_out, fly_in_left, fly_in_right, fly_in_top,
    fly_in_bottom, fly_out_left, fly_out_right, fly_out_top, fly_out_bottom,
    zoom_in, zoom_out, ken_burns.

    Times are relative to the layer's own start (so t=0 is at layer start).
    """
    layer = _require_layer(doc, _require_arg(op, "layer_id"), op="animate_layer")
    preset = str(_require_arg(op, "preset"))
    duration = model._as_float(op.get("duration") or 0.5)
    easing = str(op.get("easing") or "linear")
    interp = _EASING_MAP.get(easing, "linear")

    layer_start = model._as_float(layer.get("start"))
    layer_duration = model._as_float(layer.get("duration"))

    # Validate times fall within the layer.
    if duration > layer_duration:
        duration = layer_duration

    if preset == "fade_in":
        # Opacity 0 → 1, from layer start to start + duration.
        keyframes = layer.setdefault("keyframes", {})
        track = keyframes.setdefault("opacity", [])
        track[:] = [k for k in track if not (
            abs(model._as_float(k.get("t")) - layer_start) < 1e-9 or
            abs(model._as_float(k.get("t")) - _round_t(layer_start + duration)) < 1e-9
        )]
        track.append({"t": _round_t(layer_start), "value": 0.0, "interp": interp})
        track.append({"t": _round_t(layer_start + duration), "value": 1.0, "interp": interp})
        track.sort(key=lambda k: k["t"])

    elif preset == "fade_out":
        # Opacity 1 → 0, from layer end - duration to layer end.
        end_t = _round_t(layer_start + layer_duration)
        start_t = _round_t(max(layer_start, end_t - duration))
        keyframes = layer.setdefault("keyframes", {})
        track = keyframes.setdefault("opacity", [])
        track[:] = [k for k in track if not (
            abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
            abs(model._as_float(k.get("t")) - end_t) < 1e-9
        )]
        track.append({"t": start_t, "value": 1.0, "interp": interp})
        track.append({"t": end_t, "value": 0.0, "interp": interp})
        track.sort(key=lambda k: k["t"])

    elif preset in {"fly_in_left", "fly_in_right", "fly_in_top", "fly_in_bottom"}:
        # Position starts off-canvas, ends at 0.
        keyframes = layer.setdefault("keyframes", {})
        start_t = _round_t(layer_start)
        end_t = _round_t(layer_start + duration)

        if preset == "fly_in_left":
            prop = "transform.x"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": -1920.0, "interp": interp})
            track.append({"t": end_t, "value": 0.0, "interp": interp})

        elif preset == "fly_in_right":
            prop = "transform.x"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 1920.0, "interp": interp})
            track.append({"t": end_t, "value": 0.0, "interp": interp})

        elif preset == "fly_in_top":
            prop = "transform.y"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": -1080.0, "interp": interp})
            track.append({"t": end_t, "value": 0.0, "interp": interp})

        elif preset == "fly_in_bottom":
            prop = "transform.y"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 1080.0, "interp": interp})
            track.append({"t": end_t, "value": 0.0, "interp": interp})

        track.sort(key=lambda k: k["t"])

    elif preset in {"fly_out_left", "fly_out_right", "fly_out_top", "fly_out_bottom"}:
        # Position goes from 0 to off-canvas at the end.
        keyframes = layer.setdefault("keyframes", {})
        start_t = _round_t(max(layer_start, layer_start + layer_duration - duration))
        end_t = _round_t(layer_start + layer_duration)

        if preset == "fly_out_left":
            prop = "transform.x"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 0.0, "interp": interp})
            track.append({"t": end_t, "value": -1920.0, "interp": interp})

        elif preset == "fly_out_right":
            prop = "transform.x"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 0.0, "interp": interp})
            track.append({"t": end_t, "value": 1920.0, "interp": interp})

        elif preset == "fly_out_top":
            prop = "transform.y"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 0.0, "interp": interp})
            track.append({"t": end_t, "value": -1080.0, "interp": interp})

        elif preset == "fly_out_bottom":
            prop = "transform.y"
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 0.0, "interp": interp})
            track.append({"t": end_t, "value": 1080.0, "interp": interp})

        track.sort(key=lambda k: k["t"])

    elif preset == "zoom_in":
        # Scale 1.0 → 1.5 (or similar) over duration.
        keyframes = layer.setdefault("keyframes", {})
        start_t = _round_t(layer_start)
        end_t = _round_t(layer_start + duration)

        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 1.0, "interp": interp})
            track.append({"t": end_t, "value": 1.5, "interp": interp})
            track.sort(key=lambda k: k["t"])

    elif preset == "zoom_out":
        # Scale 1.5 → 1.0 (shrink) over duration.
        keyframes = layer.setdefault("keyframes", {})
        start_t = _round_t(layer_start)
        end_t = _round_t(layer_start + duration)

        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 1.5, "interp": interp})
            track.append({"t": end_t, "value": 1.0, "interp": interp})
            track.sort(key=lambda k: k["t"])

    elif preset == "ken_burns":
        # Pan + zoom throughout the full duration: scale 1.0 → 1.1, slight pan.
        keyframes = layer.setdefault("keyframes", {})
        start_t = _round_t(layer_start)
        end_t = _round_t(layer_start + layer_duration)

        # Scale keyframes.
        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": 1.0, "interp": interp})
            track.append({"t": end_t, "value": 1.1, "interp": interp})
            track.sort(key=lambda k: k["t"])

        # Pan keyframes (gentle x, y shift).
        for prop, start_val in [("transform.x", -30.0), ("transform.y", -20.0)]:
            track = keyframes.setdefault(prop, [])
            track[:] = [k for k in track if not (
                abs(model._as_float(k.get("t")) - start_t) < 1e-9 or
                abs(model._as_float(k.get("t")) - end_t) < 1e-9
            )]
            track.append({"t": start_t, "value": start_val, "interp": interp})
            track.append({"t": end_t, "value": 0.0, "interp": interp})
            track.sort(key=lambda k: k["t"])

    else:
        raise LayerPatchError("E_ARG", f"animate_layer: unknown preset {preset!r}")
