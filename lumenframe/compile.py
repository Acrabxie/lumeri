"""Compile a lumenframe document down to the render backend.

``compile_to_layer_stack(doc)`` turns the editable layer tree into a
:class:`gemia.video.layers.LayerStack` — the proven RGBA compositor that can
``render_frame`` / ``render_to_video``. This is what makes a document *real*:
the same doc the agent and UI edit becomes pixels.

Built-in content is produced here:

* ``solid``      — a flat canvas-sized colour fill (from ``props.color``)
* ``composition``— compiled recursively; the nested stack's ``render_frame``
  becomes the parent layer's content (precompose / nesting just works)

Everything else (``video`` / ``image`` / ``text`` / extension types) is produced
by a **resolver** you pass in: ``resolver(layer, ctx) -> content_fn | None``.
This keeps the core dependency-light and fully testable with solids, while real
media plugs in exactly where extensions and the asset pipeline live. A resolver
content_fn must return a **canvas-sized** RGBA frame (so transforms stay
centred); ``ctx`` carries ``width`` / ``height`` / ``fps`` / ``total_frames`` /
``assets``.

Mapped per layer: time (start/duration → frame range), z-order (tree order),
opacity, blend mode, centre-origin transform (translate + uniform scale +
rotation, computed analytically so rotation stays centred), opacity/position
keyframes, and alpha/luma **track mattes**.

Known M1 limitations (tracked for M1.1): non-uniform scale uses ``scale_x``;
anchor is treated as centre; adjustment-layer effects and the per-layer effect
chain are not yet applied; transform keyframes don't recouple the rotation
bounding box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from lumenframe import model

ContentFn = Callable[[int], "np.ndarray"]
Resolver = Callable[[dict[str, Any], "ResolveContext"], Optional[ContentFn]]


class CompileError(RuntimeError):
    """Raised in strict mode when a layer's content cannot be produced."""


@dataclass
class ResolveContext:
    width: int
    height: int
    fps: float
    total_frames: int
    assets: list[dict[str, Any]]

    def asset(self, asset_id: str | None) -> dict[str, Any] | None:
        if not asset_id:
            return None
        for a in self.assets:
            if str(a.get("id")) == str(asset_id):
                return a
        return None


# ── entry point ─────────────────────────────────────────────────────────


def compile_to_layer_stack(
    doc: dict[str, Any] | None,
    *,
    resolver: Resolver | None = None,
    strict: bool = False,
):
    """Compile ``doc`` into a renderable ``LayerStack``."""
    from gemia.video.layers import LayerStack  # local: pulls cv2/numpy/PIL

    norm = model.normalize_doc(doc or {})
    canvas = norm["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    fps = float(canvas["fps"])
    duration = model.doc_duration(norm)
    total_frames = max(1, int(round(duration * fps)))
    ctx = ResolveContext(width, height, fps, total_frames, norm.get("assets") or [])

    stack = LayerStack(width=width, height=height, fps=fps, total_frames=total_frames)
    _populate_stack(stack, norm["root"], ctx, resolver, strict)
    return stack


def _populate_stack(stack, comp: dict[str, Any], ctx: ResolveContext, resolver, strict) -> None:
    from gemia.video.layers import Layer

    children = comp.get("children") or []
    # Resolve content_fn for every child first so track mattes can borrow a sibling.
    resolved: dict[str, ContentFn] = {}
    matte_sources: set[str] = set()
    for layer in children:
        if isinstance(layer, dict):
            fn = _content_fn_for(layer, ctx, resolver, strict)
            if fn is not None:
                resolved[str(layer.get("id"))] = fn
            mask = layer.get("mask")
            if isinstance(mask, dict) and str(mask.get("kind")) in {"alpha_matte", "luma_matte"}:
                if mask.get("source_layer_id"):
                    matte_sources.add(str(mask["source_layer_id"]))

    for z, layer in enumerate(children):
        if not isinstance(layer, dict):
            continue
        if not layer.get("visible", True):
            continue
        # A layer used only as a track matte feeds the mask, not the canvas (AE-style).
        if str(layer.get("id")) in matte_sources:
            continue
        content_fn = resolved.get(str(layer.get("id")))
        if content_fn is None:
            continue  # nothing to draw (skipped / unresolved / adjustment / null)

        start_frame = int(round(model._as_float(layer.get("start")) * ctx.fps))
        end_frame = int(round((model._as_float(layer.get("start")) + model._as_float(layer.get("duration"))) * ctx.fps))
        transform = {**model.DEFAULT_TRANSFORM, **(layer.get("transform") or {})}
        scale = float(transform.get("scale_x", 1.0))
        rotation = float(transform.get("rotation", 0.0))
        position = _centred_position(ctx.width, ctx.height, scale, rotation,
                                     float(transform["x"]), float(transform["y"]))

        runtime = Layer(
            id=str(layer.get("id")),
            name=str(layer.get("name") or ""),
            start_frame=max(0, start_frame),
            end_frame=max(start_frame + 1, end_frame),
            z_index=z,
            blend_mode=_safe_blend(layer.get("blend_mode")),
            opacity=float(layer.get("opacity", 1.0)),
            scale=scale,
            rotation_deg=rotation,
            content_fn=content_fn,
            position=position,
            keyframes=_keyframe_tracks(layer, ctx.fps),
        )
        matte = _matte_fn(layer, resolved, children, ctx)
        if matte is not None:
            runtime.mask_fn = matte
        stack.add_layer(runtime)


# ── content producers ────────────────────────────────────────────────────


def _content_fn_for(layer: dict[str, Any], ctx: ResolveContext, resolver, strict) -> ContentFn | None:
    ltype = str(layer.get("type"))
    if ltype in {"adjustment", "null"}:
        return None  # no direct pixels in M1
    if ltype == "solid":
        return _solid_content(layer, ctx)
    if ltype == "composition":
        return _composition_content(layer, ctx, resolver, strict)
    if resolver is not None:
        fn = resolver(layer, ctx)
        if fn is not None:
            return fn
    if strict:
        raise CompileError(f"no content for layer {layer.get('id')} (type {ltype}); pass a resolver")
    return None


def _solid_content(layer: dict[str, Any], ctx: ResolveContext) -> ContentFn:
    color = _rgba01(layer.get("props", {}).get("color"), default=(0.0, 0.0, 0.0, 1.0))
    frame = np.empty((ctx.height, ctx.width, 4), dtype=np.float32)
    frame[:] = np.array(color, dtype=np.float32)

    def content_fn(_local_frame: int):
        return frame.copy()

    return content_fn


def _composition_content(layer: dict[str, Any], ctx: ResolveContext, resolver, strict) -> ContentFn:
    from gemia.video.layers import LayerStack

    dur = model._as_float(layer.get("duration"))
    sub_total = max(1, int(round(dur * ctx.fps)))
    sub = LayerStack(width=ctx.width, height=ctx.height, fps=ctx.fps, total_frames=sub_total)
    sub_ctx = ResolveContext(ctx.width, ctx.height, ctx.fps, sub_total, ctx.assets)
    _populate_stack(sub, layer, sub_ctx, resolver, strict)

    def content_fn(local_frame: int):
        idx = min(max(int(local_frame), 0), sub_total - 1)
        return sub.render_frame(idx)

    return content_fn


def _matte_fn(layer, resolved, siblings, ctx) -> Callable[[int], "np.ndarray"] | None:
    mask = layer.get("mask")
    if not isinstance(mask, dict):
        return None
    kind = str(mask.get("kind"))
    if kind not in {"alpha_matte", "luma_matte"}:
        return None  # shape masks are an M1.1 rasterisation task
    source_id = str(mask.get("source_layer_id") or "")
    source_fn = resolved.get(source_id)
    if source_fn is None:
        return None
    source_layer = next((s for s in siblings if str(s.get("id")) == source_id), None)
    masked_start = int(round(model._as_float(layer.get("start")) * ctx.fps))
    source_start = int(round(model._as_float((source_layer or {}).get("start")) * ctx.fps))
    invert = bool(mask.get("invert"))
    luma = kind == "luma_matte"

    def matte(local_frame: int):
        absolute = masked_start + int(local_frame)
        src_local = absolute - source_start
        content = source_fn(max(src_local, 0))
        rgba = np.asarray(content, dtype=np.float32)
        if luma:
            alpha = 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
            alpha = alpha * rgba[..., 3]
        else:
            alpha = rgba[..., 3]
        return 1.0 - alpha if invert else alpha

    return matte


# ── transform / keyframes ─────────────────────────────────────────────────


def _centred_position(width, height, scale, rotation_deg, x, y) -> tuple[int, int]:
    """Top-left placement that keeps canvas-sized content centred at (centre+x, centre+y).

    Accounts for the backend resizing content by ``scale`` and growing the
    rotation bounding box, so the visual centre lands where the model says.
    """
    rad = math.radians(rotation_deg)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    bound_w = (height * scale * s) + (width * scale * c)
    bound_h = (height * scale * c) + (width * scale * s)
    px = (width - bound_w) / 2.0 + x
    py = (height - bound_h) / 2.0 + y
    return (int(round(px)), int(round(py)))


#: lumenframe keyframe property -> backend Layer property the renderer reads.
_KEYFRAME_PROP_MAP = {
    "opacity": "opacity",
    "transform.x": "position_x",
    "transform.y": "position_y",
    "x": "position_x",
    "y": "position_y",
    "transform.scale_x": "scale",
    "transform.scale": "scale",
    "scale": "scale",
    "transform.rotation": "rotation_deg",
    "rotation": "rotation_deg",
}


def _keyframe_tracks(layer: dict[str, Any], fps: float):
    from gemia.video.keyframe import KeyframeTrack

    out: dict[str, Any] = {}
    for prop, points in (layer.get("keyframes") or {}).items():
        target = _KEYFRAME_PROP_MAP.get(str(prop))
        if not target or not isinstance(points, list):
            continue
        track = KeyframeTrack()
        added = 0
        for pt in points:
            if not isinstance(pt, dict) or pt.get("value") is None:
                continue
            try:
                value = float(pt["value"])
            except (TypeError, ValueError):
                continue
            frame = float(model._as_float(pt.get("t")) * fps)
            easing = _easing_for(str(pt.get("interp") or "linear"))
            track.add_keyframe(frame, value, easing)
            added += 1
        if added:
            out[target] = track
    return out


def _easing_for(interp: str) -> str:
    return {
        "linear": "linear", "hold": "step", "ease": "ease-in-out",
        "ease_in": "ease-in", "ease_out": "ease-out", "bezier": "ease-in-out",
    }.get(interp, "linear")


# ── small helpers ──────────────────────────────────────────────────────────


def _safe_blend(mode: Any) -> str:
    # The backend only implements a few modes; unknown ones degrade to normal.
    from gemia.video.layers import _blend_colors  # noqa: F401 - existence probe
    supported = {"normal", "multiply", "screen", "overlay"}
    m = str(mode or "normal")
    return m if m in supported else "normal"


def _rgba01(value: Any, *, default: tuple[float, float, float, float]):
    if isinstance(value, str) and value.startswith("#") and len(value) in (7, 9):
        hexs = value[1:]
        r = int(hexs[0:2], 16) / 255.0
        g = int(hexs[2:4], 16) / 255.0
        b = int(hexs[4:6], 16) / 255.0
        a = int(hexs[6:8], 16) / 255.0 if len(hexs) == 8 else 1.0
        return (r, g, b, a)
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        vals = [float(v) for v in value]
        if max(vals) > 1.0:
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)  # type: ignore[return-value]
    return default
