"""Self-describing catalogue of the lumenframe editing vocabulary.

One source of truth for *what the editor can do*, consumed by two audiences:

* the **agent** — :func:`describe_ops` renders a compact prompt block so natural
  language can be turned into LayerPatches;
* **third-party authors** — :func:`op_catalog` returns structured metadata they
  can introspect (and extend by registering their own ops).

A test asserts every registered core op has a catalogue entry, so the vocabulary
and its documentation can never silently drift apart.
"""
from __future__ import annotations

from typing import Any

from lumenframe.registry import list_ops, op_source

# group -> human label, used only for ordering / display
_GROUP_LABELS: dict[str, str] = {
    "layer": "Layer management",
    "time": "Time",
    "transform": "Transform & compositing",
    "interlayer": "Inter-layer (masks / clipping / adjustments)",
    "effects": "Effects & colour",
    "text": "Text styling",
    "audio": "Audio properties",
    "animation": "Animation presets",
    "keyframes": "Keyframes",
}

#: Core op metadata. ``args`` lists the meaningful keys; ``*`` marks required.
CORE_OPS_CATALOG: list[dict[str, Any]] = [
    # ── layer management ──
    {"op": "add_layer", "group": "layer", "args": ["type*", "id", "parent_id", "index", "at_time", "lane", "...fields"],
     "summary": "Create a new layer (video/image/text/shape/audio/adjustment/...) under a parent (default root)."},
    {"op": "delete_layer", "group": "layer", "args": ["layer_id*|layer_ids*"],
     "summary": "Remove one or more layers; mattes pointing at them are cleared."},
    {"op": "duplicate_layer", "group": "layer", "args": ["layer_id*", "name", "offset_time"],
     "summary": "Deep-copy a layer (fresh ids) right after the original."},
    {"op": "select", "group": "layer", "args": ["layer_ids*", "mode(replace|add|toggle|clear)"],
     "summary": "Change the editor selection."},
    {"op": "move_layer", "group": "layer", "args": ["layer_id*", "parent_id", "index", "lane", "start", "delta_start"],
     "summary": "Universal move: reparent, reorder (z), retime and relane in one op."},
    {"op": "reorder_layer", "group": "layer", "args": ["layer_id*", "to(top|bottom|forward|backward)|index|delta"],
     "summary": "Pure z-order change within the current parent."},
    {"op": "group_layers", "group": "layer", "args": ["layer_ids*", "name", "into_id"],
     "summary": "Wrap sibling layers into a new composition (precompose)."},
    {"op": "ungroup_layer", "group": "layer", "args": ["layer_id*"],
     "summary": "Dissolve a composition, lifting its children to the parent."},
    {"op": "merge_layers", "group": "layer", "args": ["layer_ids*", "name"],
     "summary": "Flatten layers into one composition marked for raster baking."},
    {"op": "rename_layer", "group": "layer", "args": ["layer_id*", "name*"], "summary": "Rename a layer."},
    {"op": "set_visibility", "group": "layer", "args": ["layer_id*", "visible*"], "summary": "Show / hide a layer."},
    {"op": "set_lock", "group": "layer", "args": ["layer_id*", "locked*"], "summary": "Lock / unlock a layer."},
    # ── time ──
    {"op": "set_time", "group": "time", "args": ["layer_id*", "start", "duration"],
     "summary": "Set a layer's start and/or duration on its parent timeline."},
    {"op": "trim", "group": "time", "args": ["layer_id*", "edge(in|out)*", "to|delta"],
     "summary": "Trim one edge; source in/out follows so media stays in sync."},
    {"op": "split", "group": "time", "args": ["layer_id*", "at_time*"],
     "summary": "Cut a layer in two at a time, splitting the source range."},
    {"op": "set_speed", "group": "time", "args": ["layer_id*", "speed*"],
     "summary": "Retime a layer; duration recomputes from the source range."},
    # ── transform & compositing ──
    {"op": "set_transform", "group": "transform", "args": ["layer_id*", "x", "y", "scale|scale_x|scale_y", "rotation", "anchor_x", "anchor_y"],
     "summary": "Move / scale / rotate a layer (anchor-relative, canvas-centre origin)."},
    {"op": "set_opacity", "group": "transform", "args": ["layer_id*", "opacity*(0..1)"], "summary": "Set layer opacity."},
    {"op": "set_blend_mode", "group": "transform", "args": ["layer_id*", "blend_mode*"], "summary": "Set the blend mode."},
    {"op": "flip_layer", "group": "transform", "args": ["layer_id*", "direction(horizontal|vertical|both)"],
     "summary": "Upsert a mirror/flip effect on the layer's effect chain."},
    # ── inter-layer ──
    {"op": "set_mask", "group": "interlayer", "args": ["layer_id*", "mask{kind(shape|alpha_matte|luma_matte), source_layer_id, shape, invert, feather}|null"],
     "summary": "Attach a drawn mask or a track matte (or clear it)."},
    {"op": "clip_to_below", "group": "interlayer", "args": ["layer_id*", "enabled"],
     "summary": "Clip a layer to the layer beneath it (clipping mask)."},
    {"op": "add_adjustment_layer", "group": "interlayer", "args": ["parent_id", "index", "name", "effects[]"],
     "summary": "Insert an adjustment layer whose effects apply to the layers below."},
    # ── effects & colour ──
    {"op": "add_effect", "group": "effects", "args": ["layer_id*", "effect{type*, params, id}", "index"],
     "summary": "Append an effect to a layer's effect chain."},
    {"op": "remove_effect", "group": "effects", "args": ["layer_id*", "effect_id*"], "summary": "Remove an effect by id."},
    {"op": "set_effect_params", "group": "effects", "args": ["layer_id*", "effect_id*", "params*", "merge"],
     "summary": "Update an effect's parameters (merge by default)."},
    {"op": "color_grade", "group": "effects", "args": ["layer_id*", "brightness", "contrast", "saturation", "exposure", "temperature", "tint", "highlights", "shadows", "gamma", "lift", "gain"],
     "summary": "Upsert a single colour-grade effect (DaVinci-style controls)."},
    {"op": "add_transition", "group": "effects", "args": ["layer_id*", "kind*", "duration", "at(in|out|both)"],
     "summary": "Attach an in/out transition to a layer."},
    # ── text styling ──
    {"op": "set_text", "group": "text", "args": ["layer_id*", "text", "font", "font_size", "color", "align", "stroke", "shadow", "background", "line_spacing"],
     "summary": "Set text properties (only provided keys are updated); works with text layers."},
    # ── audio properties ──
    {"op": "set_volume", "group": "audio", "args": ["layer_id*", "volume*"],
     "summary": "Set audio layer volume (linear, 0..1+, default 1.0)."},
    {"op": "set_audio_fade", "group": "audio", "args": ["layer_id*", "fade_in", "fade_out"],
     "summary": "Set fade-in and/or fade-out durations (seconds) on an audio layer."},
    {"op": "mute_layer", "group": "audio", "args": ["layer_id*", "muted(default true)"],
     "summary": "Mute or unmute a layer."},
    # ── animation presets ──
    {"op": "animate_layer", "group": "animation", "args": ["layer_id*", "preset*", "duration", "easing"],
     "summary": "Generate keyframes using a preset: fade_in, fade_out, fly_in/out_*, zoom_in/out, ken_burns."},
    # ── keyframes ──
    {"op": "set_keyframe", "group": "keyframes", "args": ["layer_id*", "property*", "t*", "value*", "interp"],
     "summary": "Add or replace a keyframe on a property (e.g. transform.x, opacity)."},
    {"op": "remove_keyframe", "group": "keyframes", "args": ["layer_id*", "property*", "t*"],
     "summary": "Remove a keyframe at a time."},
]

_BY_OP: dict[str, dict[str, Any]] = {entry["op"]: entry for entry in CORE_OPS_CATALOG}


def op_catalog() -> list[dict[str, Any]]:
    """Structured metadata for every registered op (core entries + extensions).

    Extension ops with no hand-written entry still appear with a generic
    placeholder so the agent at least knows they exist.
    """
    out: list[dict[str, Any]] = []
    for name in list_ops():
        entry = dict(_BY_OP.get(name, {"op": name, "group": "extension", "args": [], "summary": ""}))
        entry["source"] = op_source(name) or "extension"
        out.append(entry)
    return out


def describe_ops() -> str:
    """Compact, grouped, human-readable vocabulary block for prompt injection."""
    entries = op_catalog()
    by_group: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_group.setdefault(entry.get("group", "extension"), []).append(entry)
    lines: list[str] = ["lumenframe LayerPatch ops — {\"version\":1,\"ops\":[{\"op\":...}]}:"]
    ordered_groups = list(_GROUP_LABELS) + sorted(g for g in by_group if g not in _GROUP_LABELS)
    for group in ordered_groups:
        items = by_group.get(group)
        if not items:
            continue
        lines.append(f"\n[{_GROUP_LABELS.get(group, group.title())}]")
        for entry in sorted(items, key=lambda e: e["op"]):
            args = ", ".join(entry.get("args", []))
            lines.append(f"  {entry['op']}({args}) — {entry.get('summary', '')}")
    return "\n".join(lines)


def describe_effects() -> str:
    """List of built-in and supported effect types for the effect chain."""
    effects = [
        ("gaussian_blur", "radius(float)", "Gaussian blur applied to RGB with premultiplied alpha."),
        ("color_grade", "brightness, contrast, saturation, exposure, temperature, tint, highlights, shadows, gamma, lift, gain", "DaVinci-style colour grading."),
        ("brightness", "value(float)", "Adjust brightness."),
        ("contrast", "value(float)", "Adjust contrast."),
        ("saturation", "value(float)", "Adjust saturation (0=grey, 1=normal, >1=saturated)."),
        ("invert", "", "Invert RGB channels; alpha unchanged."),
        ("grayscale", "amount(0..1, default 1.0)", "Blend towards greyscale (0=color, 1=grey)."),
        ("mirror|flip", "direction(horizontal|vertical|both, default horizontal)", "Flip/mirror the frame."),
        ("crop", "x0, y0, x1, y1 (normalized [0,1], default 0,0,1,1)", "Crop to rectangle; outside alpha=0."),
        ("vignette", "amount(0..1, default 0.5)", "Radial edge darkening with gaussian falloff."),
        ("sharpen", "amount(float, default 1.0)", "Unsharp mask sharpening."),
        ("hue_rotate", "degrees or value(float)", "Rotate hue in HSV space."),
        ("chroma_key", "key_color(#hex or [r,g,b], default #00FF00), threshold(0..1, default 0.4), softness(0..1, default 0.1)", "Distance-based alpha keying; pixels near key_color become transparent."),
    ]
    lines = ["Effect types (used in add_effect / set_effect_params):"]
    for name, args, desc in effects:
        lines.append(f"  {name}: {args}")
        lines.append(f"    {desc}")
    return "\n".join(lines)
