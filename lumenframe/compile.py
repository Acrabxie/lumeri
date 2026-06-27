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
keyframes, the per-layer effect chain, **shape masks** (rectangle / ellipse /
polygon, rasterised in normalised canvas coordinates with optional feather +
invert), alpha/luma **track mattes**, and **adjustment layers** (After Effects
``composite-below``: the effect chain runs over the flat composite of every
layer beneath the adjustment in its comp, stacked adjustments compounding).

Known limitations (tracked): non-uniform scale collapses to ``scale_x``; anchor
is treated as centre; transform keyframes don't recouple the rotation bounding
box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from lumenframe import model, timebase

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
    """Compile ``doc`` into a renderable ``LayerStack``.

    Args:
        doc: A lumenframe document (dict with canvas, root composition, assets).
        resolver: A callable(layer, ctx) -> content_fn | None. If None, uses
            the default_resolver which handles image/video/text from assets.
        strict: If True, raises CompileError when content cannot be produced.
            If False, skips layers with unresolved content.

    Returns:
        A LayerStack ready for render_frame() / render_to_video().
    """
    from gemia.video.layers import LayerStack  # local: pulls cv2/numpy/PIL

    # Use default resolver if none provided.
    if resolver is None:
        from lumenframe.resolve import default_resolver
        resolver = default_resolver

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
    # Resolve content_fn for every non-adjustment child first so track mattes can
    # borrow a sibling.
    resolved: dict[str, ContentFn] = {}
    matte_sources: set[str] = set()

    for layer in children:
        if isinstance(layer, dict):
            if str(layer.get("type", "")) != "adjustment":
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

        ltype = str(layer.get("type", ""))
        if ltype == "adjustment":
            # After Effects composite-below: an adjustment layer's effect chain runs
            # over the flat composite of EVERY layer beneath it in this comp — not
            # just the nearest one. We render that composite as a sub-stack
            # (recursively, so adjustments stacked above each other compound over the
            # cumulative result), then apply the adjustment's effects on top. The
            # adjustment is a real drawn layer: its own opacity / blend / time range /
            # mask govern how the effected composite lands back over the layers below
            # (full opacity + normal blend = a straight replacement, exactly like AE;
            # a shape mask localises the effect to a region).
            content_fn = _adjustment_content(children[:z], layer, ctx, resolver, strict)
            effects = layer.get("effects") or []
            if effects:
                content_fn = _wrap_with_effects(content_fn, effects, ctx)
        else:
            # A layer used only as a track matte feeds the mask, not the canvas.
            if str(layer.get("id")) in matte_sources:
                continue
            content_fn = resolved.get(str(layer.get("id")))
            if content_fn is None:
                continue  # nothing to draw (skipped / unresolved / null)

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
            # Pass expressions for time-driven property animation
            expressions=layer.get("expressions") or {},
        )
        matte = _matte_fn(layer, resolved, children, ctx)
        if matte is not None:
            runtime.mask_fn = matte
        time_map = _time_remap_fn(layer, ctx.fps)
        if time_map is not None:
            runtime.time_map_fn = time_map
        stack.add_layer(runtime)


def _adjustment_content(below_children, adj_layer, ctx: ResolveContext, resolver, strict) -> ContentFn:
    """Composite-below source for an adjustment layer.

    ``below_children`` are the comp children physically beneath the adjustment, in
    their original (parent-comp) timeline. They are populated into a sub-stack that
    shares the parent's frame timeline (recursively applying adjustment semantics,
    so a lower adjustment is already baked into this composite). The adjustment's
    effect chain is applied by the caller; here we only produce the flat backdrop.

    The Layer machinery hands ``content_fn`` a layer-local frame (absolute minus the
    adjustment's start), so we shift it back to the parent-absolute frame before
    sampling the sub-stack. NOTE: each adjustment re-renders everything below it, so
    deeply stacked adjustments cost O(n²) — acceptable, and inherent to the model.
    """
    from gemia.video.layers import LayerStack

    sub = LayerStack(width=ctx.width, height=ctx.height, fps=ctx.fps,
                     total_frames=ctx.total_frames)
    _populate_stack(sub, {"children": list(below_children)}, ctx, resolver, strict)
    adj_start = int(round(model._as_float(adj_layer.get("start")) * ctx.fps))
    last = max(0, ctx.total_frames - 1)

    def content_fn(local_frame: int):
        absolute = min(max(int(local_frame) + adj_start, 0), last)
        return np.asarray(sub.render_frame(absolute), dtype=np.float32).copy()

    return content_fn


# ── content producers ────────────────────────────────────────────────────


def _content_fn_for(layer: dict[str, Any], ctx: ResolveContext, resolver, strict) -> ContentFn | None:
    ltype = str(layer.get("type"))
    if ltype in {"adjustment", "null"}:
        return None  # no direct pixels in M1.1; adjustment layers are handled separately
    if ltype == "solid":
        fn = _solid_content(layer, ctx)
    elif ltype == "composition":
        fn = _composition_content(layer, ctx, resolver, strict)
    elif resolver is not None:
        fn = resolver(layer, ctx)
    else:
        fn = None

    if fn is None:
        if strict:
            raise CompileError(f"no content for layer {layer.get('id')} (type {ltype}); pass a resolver")
        return None

    # Wrap the content_fn to apply per-layer effects (the effect chain).
    effects = layer.get("effects") or []
    if effects:
        fn = _wrap_with_effects(fn, effects, ctx)

    return fn


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
    if kind == "shape":
        return _shape_matte(mask, ctx)
    if kind not in {"alpha_matte", "luma_matte"}:
        return None
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


# ── shape masks ───────────────────────────────────────────────────────────


def _shape_matte(mask: dict[str, Any], ctx: ResolveContext) -> Callable[[int], "np.ndarray"]:
    """A drawn vector mask (rectangle / ellipse / polygon) → per-frame alpha.

    The shape is rasterised once in canvas space (it does not animate yet), so
    the closure just returns the cached alpha. ``feather`` and ``invert`` are
    baked in; the backend resizes the alpha onto the (transformed) layer frame,
    so a mask travels with its layer's transform — the After Effects semantic.
    """
    alpha = _rasterise_shape_mask(mask, ctx.width, ctx.height)

    def matte(_local_frame: int):
        return alpha

    return matte


def _shape_box(shape: dict[str, Any]) -> tuple[float, float, float, float]:
    """Normalised ``(x0, y0, x1, y1)`` bounding box for a rect/ellipse shape.

    Accepts ``x0/y0/x1/y1``, a ``rect`` list, or a centre form
    (``cx/cy`` + ``rx/ry`` or ``w/h``). Defaults to the full canvas.
    """
    rect = shape.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 4:
        return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    if all(k in shape for k in ("x0", "y0", "x1", "y1")):
        return (float(shape["x0"]), float(shape["y0"]), float(shape["x1"]), float(shape["y1"]))
    if "cx" in shape and "cy" in shape and any(k in shape for k in ("rx", "ry", "w", "h")):
        cx, cy = float(shape["cx"]), float(shape["cy"])
        half_w = float(shape["w"]) / 2.0 if "w" in shape else float(shape.get("rx", shape.get("ry", 0.0)))
        half_h = float(shape["h"]) / 2.0 if "h" in shape else float(shape.get("ry", shape.get("rx", 0.0)))
        return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
    return (0.0, 0.0, 1.0, 1.0)


def _filled_rounded_rect(img: "np.ndarray", x0: int, y0: int, x1: int, y1: int, r: int) -> None:
    import cv2

    if x1 <= x0 or y1 <= y0:
        return
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    if r <= 0:
        cv2.rectangle(img, (x0, y0), (x1, y1), 255, -1)
        return
    cv2.rectangle(img, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(img, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(img, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)


def _rasterise_shape_mask(mask: dict[str, Any], width: int, height: int) -> "np.ndarray":
    """Rasterise a shape-mask spec to a float32 ``(H, W)`` alpha in ``[0, 1]``.

    Coordinates are normalised to the canvas (``[0, 1]``) so a mask is
    resolution-independent. ``feather`` (a fraction of the smaller canvas
    dimension) softens the edge; ``invert`` flips coverage.
    """
    import cv2

    shape = mask.get("shape") if isinstance(mask.get("shape"), dict) else {}
    stype = str(shape.get("type") or "rectangle").lower()
    img = np.zeros((height, width), dtype=np.uint8)

    def _px(nx: Any, ny: Any) -> tuple[int, int]:
        return (int(round(float(nx) * width)), int(round(float(ny) * height)))

    if stype in {"polygon", "poly"}:
        pts = [
            _px(p[0], p[1])
            for p in (shape.get("points") or [])
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(pts) >= 3:
            cv2.fillPoly(img, [np.array(pts, dtype=np.int32)], 255, lineType=cv2.LINE_AA)
    else:
        x0n, y0n, x1n, y1n = _shape_box(shape)
        x0, y0 = _px(x0n, y0n)
        x1, y1 = _px(x1n, y1n)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if stype in {"ellipse", "circle", "oval"}:
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            ax, ay = (x1 - x0) // 2, (y1 - y0) // 2
            if ax > 0 and ay > 0:
                cv2.ellipse(img, (cx, cy), (ax, ay), 0, 0, 360, 255, -1, lineType=cv2.LINE_AA)
        else:  # rectangle (default)
            radius = float(shape.get("radius") or 0.0)
            r = int(round(radius * min(width, height)))
            if r > 0:
                _filled_rounded_rect(img, x0, y0, x1, y1, r)
            elif x1 > x0 and y1 > y0:
                cv2.rectangle(img, (x0, y0), (x1, y1), 255, -1)

    alpha = img.astype(np.float32) / 255.0
    feather = float(mask.get("feather") or 0.0)
    if feather > 0:
        sigma = feather * min(width, height)
        if sigma > 0:
            alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_DEFAULT)
    if mask.get("invert"):
        alpha = 1.0 - alpha
    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


def _resolve_property_with_expression(
    layer: dict,
    property_name: str,
    frame_index: int,
    fps: float,
    canvas_width: float,
    canvas_height: float,
    default: float = 0.0,
) -> float:
    """Resolve a layer property value, prioritizing expressions over keyframes.

    Precedence: expression (time-driven) > keyframes > static value > default
    
    Args:
        layer: Layer dict that may have "expressions" and "keyframes"
        property_name: Property like "opacity" or "transform.x"
        frame_index: Current frame number
        fps: Frames per second
        canvas_width, canvas_height: Canvas dimensions for binding
        default: Fallback value if no binding found
    
    Returns:
        Evaluated property value as float
    """
    from gemia.expressions import SafeEvaluator, ExprError
    
    # Check if property has an expression bound
    expressions = layer.get("expressions") or {}
    if property_name in expressions:
        expr_data = expressions[property_name]
        expr_str = expr_data.get("expr", "")
        
        try:
            time_sec = frame_index / fps
            layer_duration = float(layer.get("duration", 1.0))
            evaluator = SafeEvaluator(
                time=time_sec,
                duration=layer_duration,
                width=canvas_width,
                height=canvas_height,
            )
            result = evaluator.eval(expr_str)
            return float(result)
        except ExprError:
            # If expression fails, fall through to keyframes
            pass
        except Exception:
            # Any other error, fall through
            pass
    
    # If no expression, check keyframes (this is handled by the caller via KeyframeTrack)
    # Fallback to static value
    static = layer.get(property_name) if property_name not in ("transform.x", "transform.y", "transform.rotation", "transform.scale_x", "transform.scale_y") else None
    if static is not None:
        return float(static)
    
    return default



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


def _time_remap_fn(layer: dict[str, Any], fps: float) -> Callable[[int], int] | None:
    """Build the ``time_map_fn`` for a layer carrying a ``time_remap`` curve.

    The render backend hands ``time_map_fn`` the *output-local* frame (absolute
    frame minus the layer's ``start_frame``) and feeds its return value — the
    *source-local* frame — to the layer's ``content_fn``. So:

        out_sec    = output_local_frame / fps                 (output timeline)
        src_sec    = eval_time_remap(curve, out_sec)          (source timeline)
        src_local  = to_frame(src_sec - source_in, fps)       (content_fn arg)

    This drives content sampling only; transform / opacity keyframes and
    expressions stay on the OUTPUT frame (the backend evaluates those off the
    absolute frame index, untouched by this map). A ``time_remap`` therefore
    overrides any constant-speed source mapping for the same layer.
    """
    remap = layer.get("time_remap")
    if not isinstance(remap, dict) or not (remap.get("keyframes")):
        return None
    source_in = model._as_float(layer.get("source_in"))

    def time_map_fn(output_local_frame: int) -> int:
        out_sec = timebase.to_seconds(int(output_local_frame), fps)
        src_sec = model.eval_time_remap(remap, out_sec)
        return max(0, timebase.to_frame(src_sec - source_in, fps))

    return time_map_fn


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


# ── effect chain application ──────────────────────────────────────────────


def _wrap_with_effects(base_fn: ContentFn, effects: list[dict[str, Any]], ctx: ResolveContext) -> ContentFn:
    """Wrap a content_fn to apply per-layer effects in order.

    Args:
        base_fn: The original content_fn(frame_index) -> RGBAFrame.
        effects: List of effect dicts {type, params, enabled}.
        ctx: ResolveContext for canvas dimensions.

    Returns:
        A new content_fn that applies effects in order.
    """
    def wrapped_fn(frame_index: int) -> np.ndarray:
        frame = base_fn(frame_index)
        for effect in effects:
            if not effect.get("enabled", True):
                continue
            eff_type = str(effect.get("type", ""))
            params = dict(effect.get("params") or {})
            frame = _apply_effect(frame, eff_type, params, ctx)
        return frame

    return wrapped_fn


# ── effect dispatch table ─────────────────────────────────────────────────
#
# Each built-in effect is a small adapter ``fn(frame, params, ctx) -> frame``
# that reads its params (with the same defaults as before) and calls the
# underlying ``_apply_*`` kernel. The ``EFFECTS`` dict maps an effect *type*
# string to its adapter — adding a new effect is now "a new function + one dict
# entry", with no edits to the dispatch logic in :func:`_apply_effect`.
#
# Aliases (e.g. ``flip`` for ``mirror``) are real, separate keys pointing at the
# same adapter, so the table's key set is the canonical effect-type vocabulary
# (which :mod:`lumenframe.catalog` is checked against by a drift guard test).


def _effect_gaussian_blur(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    radius = float(params.get("radius", 5.0))
    return _apply_gaussian_blur_rgba(frame, radius)


def _effect_color_grade(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    brightness = float(params.get("brightness", 0.0))
    contrast = float(params.get("contrast", 1.0))
    saturation = float(params.get("saturation", 1.0))
    return _apply_color_grade(frame, brightness, contrast, saturation)


def _effect_brightness(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    brightness = float(params.get("value", 0.0))
    return _apply_color_grade(frame, brightness, 1.0, 1.0)


def _effect_contrast(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    contrast = float(params.get("value", 1.0))
    return _apply_color_grade(frame, 0.0, contrast, 1.0)


def _effect_saturation(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    saturation = float(params.get("value", 1.0))
    return _apply_color_grade(frame, 0.0, 1.0, saturation)


def _effect_invert(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    return _apply_invert(frame)


def _effect_grayscale(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    amount = float(params.get("amount", 1.0))
    return _apply_grayscale(frame, amount)


def _effect_mirror(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    direction = str(params.get("direction", "horizontal"))
    return _apply_mirror(frame, direction)


def _effect_crop(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    x0 = float(params.get("x0", 0.0))
    y0 = float(params.get("y0", 0.0))
    x1 = float(params.get("x1", 1.0))
    y1 = float(params.get("y1", 1.0))
    return _apply_crop(frame, x0, y0, x1, y1)


def _effect_vignette(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    amount = float(params.get("amount", 0.5))
    return _apply_vignette(frame, amount)


def _effect_sharpen(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    amount = float(params.get("amount", 1.0))
    return _apply_sharpen(frame, amount)


def _effect_hue_rotate(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    degrees = float(params.get("degrees") or params.get("value") or 0.0)
    return _apply_hue_rotate(frame, degrees)


def _effect_chroma_key(frame: np.ndarray, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    key_color = params.get("key_color", "#00FF00")
    threshold = float(params.get("threshold", 0.4))
    softness = float(params.get("softness", 0.1))
    return _apply_chroma_key(frame, key_color, threshold, softness)


EffectFn = Callable[["np.ndarray", dict[str, Any], "ResolveContext"], "np.ndarray"]

#: Built-in effect dispatch table: effect type -> adapter ``fn(frame, params, ctx)``.
#: This is the single source of truth for the built-in effect vocabulary; a new
#: effect is a new ``_effect_*`` function plus one entry here — no if/elif edits.
#: ``flip`` is an alias of ``mirror`` (same adapter, behaviour-preserving).
EFFECTS: dict[str, EffectFn] = {
    "gaussian_blur": _effect_gaussian_blur,
    "color_grade": _effect_color_grade,
    "brightness": _effect_brightness,
    "contrast": _effect_contrast,
    "saturation": _effect_saturation,
    "invert": _effect_invert,
    "grayscale": _effect_grayscale,
    "mirror": _effect_mirror,
    "flip": _effect_mirror,
    "crop": _effect_crop,
    "vignette": _effect_vignette,
    "sharpen": _effect_sharpen,
    "hue_rotate": _effect_hue_rotate,
    "chroma_key": _effect_chroma_key,
}


def _apply_effect(frame: np.ndarray, effect_type: str, params: dict[str, Any], ctx: ResolveContext) -> np.ndarray:
    """Apply a single effect to an RGBA frame.

    Built-in effects are dispatched through the :data:`EFFECTS` table; effects
    not in the table fall back to the third-party :mod:`gemia.registry`, and an
    unknown / failing effect is silently skipped (behaviour-preserving).
    """
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    # Built-in effects via dispatch table.
    fn = EFFECTS.get(effect_type)
    if fn is not None:
        return fn(frame, params, ctx)

    # Attempt to resolve via gemia registry (for extensions).
    try:
        from gemia.registry import resolve
        func = resolve(effect_type)
        # Call the function with the BGR image (without alpha).
        bgr = frame[..., :3]
        alpha = frame[..., 3:4]
        processed = func(bgr, **params)
        return np.concatenate([processed, alpha], axis=2).astype(np.float32)
    except Exception:
        # Unknown effect or resolution failed; skip silently.
        return frame


def _apply_gaussian_blur_rgba(frame: np.ndarray, radius: float) -> np.ndarray:
    """Apply Gaussian blur to RGBA frame using premultiplied alpha."""
    import cv2

    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    if radius <= 0:
        return frame

    # Separate RGB and alpha.
    rgb = frame[..., :3]
    alpha = frame[..., 3:4]

    # Premultiply: rgb * alpha to avoid haloing at semi-transparent edges.
    rgb_premult = rgb * alpha

    # Apply blur to premultiplied RGB and alpha separately.
    ksize = int(2 * round(radius) + 1)
    blurred_rgb_premult = cv2.GaussianBlur(rgb_premult, (ksize, ksize), radius)
    blurred_alpha_2d = cv2.GaussianBlur(alpha[..., 0], (ksize, ksize), radius)
    blurred_alpha = blurred_alpha_2d[..., np.newaxis]

    # Unpremultiply: divide rgb back by alpha (avoid division by zero).
    eps = 1e-6
    blurred_rgb = blurred_rgb_premult / (blurred_alpha + eps)
    blurred_rgb = np.clip(blurred_rgb, 0.0, 1.0)

    return np.concatenate([blurred_rgb, blurred_alpha], axis=2).astype(np.float32)


def _apply_color_grade(frame: np.ndarray, brightness: float, contrast: float, saturation: float) -> np.ndarray:
    """Apply brightness, contrast, and saturation adjustments to RGBA frame."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    rgb = frame[..., :3].copy()
    alpha = frame[..., 3:4]

    # Apply brightness (add to all channels).
    rgb = np.clip(rgb + brightness, 0.0, 1.0)

    # Apply contrast (scale around 0.5).
    rgb = 0.5 + contrast * (rgb - 0.5)
    rgb = np.clip(rgb, 0.0, 1.0)

    # Apply saturation (desaturate by blending towards greyscale).
    if not np.isclose(saturation, 1.0):
        # Compute greyscale and blend with original.
        grey = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        grey = np.stack([grey, grey, grey], axis=2)
        rgb = (1.0 - saturation) * grey + saturation * rgb
        rgb = np.clip(rgb, 0.0, 1.0)

    return np.concatenate([rgb, alpha], axis=2).astype(np.float32)


def _apply_invert(frame: np.ndarray) -> np.ndarray:
    """Invert RGB channels, preserve alpha."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    rgb = frame[..., :3]
    alpha = frame[..., 3:4]
    inverted_rgb = 1.0 - rgb
    return np.concatenate([inverted_rgb, alpha], axis=2).astype(np.float32)


def _apply_grayscale(frame: np.ndarray, amount: float) -> np.ndarray:
    """Blend towards greyscale; amount 0=full color, 1=full grey."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    rgb = frame[..., :3].copy()
    alpha = frame[..., 3:4]

    amount = max(0.0, min(1.0, amount))
    if amount > 0.0:
        grey = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        grey = np.stack([grey, grey, grey], axis=2)
        rgb = (1.0 - amount) * rgb + amount * grey
        rgb = np.clip(rgb, 0.0, 1.0)

    return np.concatenate([rgb, alpha], axis=2).astype(np.float32)


def _apply_mirror(frame: np.ndarray, direction: str) -> np.ndarray:
    """Flip/mirror along horizontal, vertical, or both axes."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    direction = str(direction).lower()
    if direction in {"horizontal", "h", "x"}:
        return np.flip(frame, axis=1).copy().astype(np.float32)
    elif direction in {"vertical", "v", "y"}:
        return np.flip(frame, axis=0).copy().astype(np.float32)
    elif direction in {"both", "xy", "all"}:
        return np.flip(np.flip(frame, axis=0), axis=1).copy().astype(np.float32)
    return frame


def _apply_crop(frame: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Crop to normalized rectangle [x0, y0, x1, y1] ∈ [0,1]; outside α=0."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    h, w = frame.shape[:2]
    x0, y0, x1, y1 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, y0)), max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
    if x0 >= x1 or y0 >= y1:
        # Crop area is degenerate; return fully transparent.
        return np.zeros_like(frame, dtype=np.float32)

    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = int(x1 * w), int(y1 * h)
    px0, py0, px1, py1 = max(0, px0), max(0, py0), min(w, px1), min(h, py1)

    result = np.zeros_like(frame, dtype=np.float32)
    if px0 < px1 and py0 < py1:
        result[py0:py1, px0:px1] = frame[py0:py1, px0:px1]
    return result


def _apply_vignette(frame: np.ndarray, amount: float) -> np.ndarray:
    """Apply radial edge darkening with gaussian falloff from center."""
    import cv2

    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    h, w = frame.shape[:2]
    amount = max(0.0, min(1.0, amount))

    if amount == 0.0:
        return frame

    # Create a gaussian falloff from center outward.
    cy, cx = h / 2.0, w / 2.0
    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    normalized_dist = np.clip(dist / max_dist, 0.0, 1.0)

    # Gaussian falloff: 1.0 at center, 0.0 at edges.
    falloff = np.exp(-2.0 * (normalized_dist ** 2))
    # Blend between 1.0 (no vignette) and falloff (full vignette).
    vignette = 1.0 - amount * (1.0 - falloff)
    vignette = np.stack([vignette, vignette, vignette, np.ones((h, w))], axis=2)

    rgb = frame[..., :3] * vignette[..., :3]
    alpha = frame[..., 3:4]
    return np.concatenate([rgb, alpha], axis=2).astype(np.float32)


def _apply_sharpen(frame: np.ndarray, amount: float) -> np.ndarray:
    """Apply unsharp mask: original + amount*(original - gaussian_blur)."""
    import cv2

    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    amount = max(0.0, amount)
    if amount == 0.0:
        return frame

    rgb = frame[..., :3]
    alpha = frame[..., 3:4]

    # Gaussian blur with radius 2.
    ksize = 5
    blurred = cv2.GaussianBlur(rgb, (ksize, ksize), 1.0)

    # Unsharp mask.
    sharpened = rgb + amount * (rgb - blurred)
    sharpened = np.clip(sharpened, 0.0, 1.0)

    return np.concatenate([sharpened, alpha], axis=2).astype(np.float32)


def _apply_hue_rotate(frame: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate hue in HSV space."""
    import cv2

    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    rgb = frame[..., :3]
    alpha = frame[..., 3:4]

    # Convert to HSV. OpenCV expects BGR uint8, so convert.
    bgr_uint8 = (rgb[..., ::-1] * 255.0).astype(np.uint8)  # RGB -> BGR, float to uint8
    hsv = cv2.cvtColor(bgr_uint8, cv2.COLOR_BGR2HSV_FULL)  # HSV in [0,255]

    # Rotate hue channel.
    degrees = float(degrees)
    hue_shift = int((degrees / 360.0) * 255.0) % 256
    hsv[..., 0] = (hsv[..., 0].astype(np.int32) + hue_shift) % 256

    # Convert back.
    bgr_out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR_FULL)
    rgb_out = bgr_out[..., ::-1]  # BGR -> RGB
    rgb_out = rgb_out.astype(np.float32) / 255.0

    return np.concatenate([rgb_out, alpha], axis=2).astype(np.float32)


def _apply_chroma_key(frame: np.ndarray, key_color: str | tuple, threshold: float, softness: float) -> np.ndarray:
    """Distance-based alpha keying: pixels near key_color become transparent."""
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame

    # Parse key_color.
    key_rgb = _rgba01(key_color, default=(0.0, 1.0, 0.0, 1.0))[:3]
    key_rgb = np.array(key_rgb, dtype=np.float32)

    rgb = frame[..., :3]
    alpha = frame[..., 3:4]

    # Euclidean distance in RGB space.
    dist = np.sqrt(np.sum((rgb - key_rgb) ** 2, axis=2, keepdims=True))

    # Distance-based keying: 0 when dist < threshold, 1 when dist > threshold+softness.
    threshold = max(0.0, threshold)
    softness = max(0.0, softness)
    edge = threshold + softness
    if edge <= threshold:
        # No softness; hard boundary.
        key_alpha = (dist > threshold).astype(np.float32)
    else:
        # Softness ramp.
        key_alpha = np.clip((dist - threshold) / softness, 0.0, 1.0)

    # Multiply into existing alpha.
    new_alpha = alpha * key_alpha
    return np.concatenate([rgb, new_alpha], axis=2).astype(np.float32)
