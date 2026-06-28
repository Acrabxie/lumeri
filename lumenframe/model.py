"""Canonical, JSON-serialisable document model for lumenframe.

The whole editable project is a plain ``dict`` (a ``LumenDoc``) so it round-trips
over JSON / SSE / disk with zero conversion — the same choice the timeline model
makes in :mod:`gemia.project_model`. A ``LayerNode`` is likewise a ``dict``.

Layer tree & stacking order
---------------------------
``root`` is a ``composition`` layer whose ``children`` are the project's layers.
A child's ``children`` list is ordered **bottom → top**: index ``0`` composites
first (at the back), the *last* child composites on top. This matches the render
backend (:class:`gemia.video.layers.LayerStack`, which sorts ascending and blends
in order). Editor panels are free to display the reverse; storage stays one way.

Time
----
Every layer carries its own time on its parent's local timeline:
``start`` + ``duration`` place it; ``source_in`` / ``source_out`` trim into the
source media; ``speed`` retimes it. A ``composition`` has its own local origin,
so nesting (precompose) is just another layer with children.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterator

# ── vocabulary ──────────────────────────────────────────────────────────

#: Built-in layer types. Third-party repos add more via the registry.
LAYER_TYPES: set[str] = {
    "composition",  # container: holds children on its own local timeline
    "video",
    "image",
    "audio",
    "text",
    "shape",
    "gradient",  # canvas-sized linear/radial gradient fill (resolver-rendered)
    "sticker",
    "adjustment",  # carries effects that apply to the layers *below* it
    "solid",       # flat colour fill
    "null",        # transform-only parent / rig, never rendered
}

#: Layer types that may hold ``children``.
CONTAINER_TYPES: set[str] = {"composition"}

#: Blend modes the core understands (the render backend may support a subset;
#: unknown modes from extensions degrade to ``normal`` at compile time).
BLEND_MODES: set[str] = {
    "normal", "multiply", "screen", "overlay", "darken", "lighten",
    "color_dodge", "color_burn", "hard_light", "soft_light", "difference",
    "exclusion", "add", "subtract",
}

#: Interpolation kinds for keyframes.
INTERP_KINDS: set[str] = {"linear", "hold", "ease", "ease_in", "ease_out", "bezier"}

#: Mask kinds. ``shape`` is a drawn/vector mask; ``alpha_matte`` / ``luma_matte``
#: borrow another sibling layer as a track matte.
MASK_KINDS: set[str] = {"shape", "alpha_matte", "luma_matte"}

DEFAULT_CANVAS: dict[str, Any] = {
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "background": "#000000",
}

#: Transform origin convention: ``x`` / ``y`` offset the layer's anchor from the
#: canvas centre, in pixels (so ``0, 0`` is dead-centre — matching CapCut).
#: ``anchor_x`` / ``anchor_y`` are normalised within the layer (``0.5`` = centre).
DEFAULT_TRANSFORM: dict[str, float] = {
    "x": 0.0,
    "y": 0.0,
    "scale_x": 1.0,
    "scale_y": 1.0,
    "rotation": 0.0,
    "anchor_x": 0.5,
    "anchor_y": 0.5,
}

#: Rounding for all time values written into the doc (mirrors timeline v1).
TIME_NDIGITS = 6

#: Extrapolation policies for a time-remap curve outside its keyframe span.
TIME_REMAP_EXTRAPOLATE: set[str] = {"hold", "loop", "pingpong"}

#: Interpolation kinds a time-remap keyframe may use. A remap curve is a plain
#: output_seconds -> source_seconds mapping, so only the simplest, exactly
#: invertible kinds are allowed: ``linear`` ramps source time, ``hold`` freezes
#: it (a still frame) until the next keyframe.
TIME_REMAP_INTERP: set[str] = {"linear", "hold"}

#: Canonical top-level keys on a layer. Anything else an author supplies is
#: folded into ``props`` rather than dropped.
_SCHEMA_KEYS: frozenset[str] = frozenset({
    "id", "type", "name", "children", "start", "duration", "source_in",
    "source_out", "speed", "lane", "transform", "opacity", "blend_mode",
    "visible", "locked", "mask", "clip_to_below", "merged", "asset_id",
    "effects", "keyframes", "time_remap", "props",
})


def gen_id(prefix: str = "layer") -> str:
    """Short, collision-resistant id used for layers / effects / assets."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── factories ───────────────────────────────────────────────────────────


def new_layer(
    layer_type: str = "composition",
    *,
    id: str | None = None,
    name: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a fresh, fully-defaulted layer dict of ``layer_type``.

    Unknown keys land in ``props`` so type-specific data (text config, shape
    geometry, extension fields) never gets lost. Known schema keys override
    their defaults directly.
    """
    layer: dict[str, Any] = {
        "id": id or gen_id(_id_prefix(layer_type)),
        "type": str(layer_type),
        "name": name if name is not None else _default_name(layer_type),
        "children": [],
        "start": 0.0,
        "duration": 0.0,
        "source_in": 0.0,
        "source_out": 0.0,
        "speed": 1.0,
        "lane": 0,
        "transform": dict(DEFAULT_TRANSFORM),
        "opacity": 1.0,
        "blend_mode": "normal",
        "visible": True,
        "locked": False,
        "mask": None,
        "clip_to_below": False,
        "effects": [],
        "keyframes": {},
        "asset_id": None,
        "props": {},
        "merged": False,
    }
    props: dict[str, Any] = {}
    for key, value in overrides.items():
        if key == "transform" and isinstance(value, dict):
            layer["transform"] = {**DEFAULT_TRANSFORM, **value}
        elif key in layer:
            layer[key] = value
        else:
            props[key] = value
    if props:
        layer["props"] = {**layer["props"], **props}
    return layer


def empty_doc(
    *,
    title: str = "Untitled",
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    background: str | None = None,
) -> dict[str, Any]:
    """A minimal valid document: one empty root composition."""
    canvas = dict(DEFAULT_CANVAS)
    if width is not None:
        canvas["width"] = int(width)
    if height is not None:
        canvas["height"] = int(height)
    if fps is not None:
        canvas["fps"] = float(fps)
    if background is not None:
        canvas["background"] = str(background)
    root = new_layer("composition", id="root", name="Root")
    return {
        "version": 1,
        "id": gen_id("doc"),
        "title": str(title),
        "canvas": canvas,
        "root": root,
        "assets": [],
        "selection": [],
    }


# ── normalisation ───────────────────────────────────────────────────────


def normalize_doc(doc: dict[str, Any] | None) -> dict[str, Any]:
    """Return a fully-defaulted, type-coerced copy of ``doc``.

    Tolerant of partial / hand-authored input: missing keys are filled, the root
    is forced to a composition, every layer is recursively normalised. Never
    mutates the input.
    """
    src = doc if isinstance(doc, dict) else {}
    canvas_src = src.get("canvas") if isinstance(src.get("canvas"), dict) else {}
    canvas = {**DEFAULT_CANVAS, **canvas_src}
    canvas["width"] = int(canvas.get("width") or DEFAULT_CANVAS["width"])
    canvas["height"] = int(canvas.get("height") or DEFAULT_CANVAS["height"])
    canvas["fps"] = float(canvas.get("fps") or DEFAULT_CANVAS["fps"])
    canvas["background"] = str(canvas.get("background") or DEFAULT_CANVAS["background"])

    root_src = src.get("root") if isinstance(src.get("root"), dict) else None
    if root_src is None:
        root = new_layer("composition", id="root", name="Root")
    else:
        root = _normalize_layer(root_src, force_type="composition")
        if not root.get("id"):
            root["id"] = "root"

    assets = [a for a in (src.get("assets") or []) if isinstance(a, dict)]
    known = _collect_ids(root)
    selection = [str(i) for i in (src.get("selection") or []) if str(i) in known]

    return {
        "version": int(src.get("version") or 1),
        "id": str(src.get("id") or gen_id("doc")),
        "title": str(src.get("title") or "Untitled"),
        "canvas": canvas,
        "root": root,
        "assets": assets,
        "selection": selection,
    }


def _normalize_layer(raw: dict[str, Any], *, force_type: str | None = None) -> dict[str, Any]:
    layer_type = force_type or str(raw.get("type") or "composition")
    base = new_layer(layer_type, id=str(raw.get("id") or "") or None, name=raw.get("name"))
    for key in (
        "start", "duration", "source_in", "source_out", "speed",
        "opacity",
    ):
        if raw.get(key) is not None:
            base[key] = _as_float(raw.get(key))
    if raw.get("lane") is not None:
        base["lane"] = int(_as_float(raw.get("lane")))
    if isinstance(raw.get("transform"), dict):
        base["transform"] = {**DEFAULT_TRANSFORM, **{
            k: _as_float(v) for k, v in raw["transform"].items()
        }}
    if raw.get("blend_mode"):
        base["blend_mode"] = str(raw["blend_mode"])
    base["visible"] = bool(raw.get("visible", True))
    base["locked"] = bool(raw.get("locked", False))
    base["clip_to_below"] = bool(raw.get("clip_to_below", False))
    base["merged"] = bool(raw.get("merged", False))
    if raw.get("asset_id") is not None:
        base["asset_id"] = str(raw["asset_id"])
    if isinstance(raw.get("mask"), dict):
        base["mask"] = _normalize_mask(raw["mask"])
    if isinstance(raw.get("effects"), list):
        base["effects"] = [_normalize_effect(e) for e in raw["effects"] if isinstance(e, dict)]
    if isinstance(raw.get("expressions"), dict):
        base["expressions"] = {
            k: v for k, v in raw["expressions"].items() if isinstance(v, dict)
        }
    if isinstance(raw.get("keyframes"), dict):
        base["keyframes"] = _normalize_keyframes(raw["keyframes"])
    if isinstance(raw.get("time_remap"), dict):
        remap = _normalize_time_remap(raw["time_remap"])
        if remap is not None:
            base["time_remap"] = remap
    if isinstance(raw.get("props"), dict):
        base["props"] = dict(raw["props"])
    # Be forgiving with hand-/agent-authored layers: stash any unknown top-level
    # key into props so type-specific data (e.g. text="hi") is never dropped.
    for key, value in raw.items():
        if key not in _SCHEMA_KEYS:
            base["props"].setdefault(key, value)
    children = raw.get("children")
    if isinstance(children, list):
        base["children"] = [
            _normalize_layer(c) for c in children if isinstance(c, dict)
        ]
    return base


def _normalize_mask(mask: dict[str, Any]) -> dict[str, Any]:
    kind = str(mask.get("kind") or "shape")
    out: dict[str, Any] = {
        "kind": kind if kind in MASK_KINDS else "shape",
        "invert": bool(mask.get("invert", False)),
        "feather": _as_float(mask.get("feather")),
    }
    if mask.get("source_layer_id"):
        out["source_layer_id"] = str(mask["source_layer_id"])
    if isinstance(mask.get("shape"), dict):
        out["shape"] = dict(mask["shape"])
    return out


def _normalize_effect(effect: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(effect.get("id") or gen_id("fx")),
        "type": str(effect.get("type") or "unknown"),
        "params": dict(effect.get("params") or {}),
        "enabled": bool(effect.get("enabled", True)),
    }


def _normalize_keyframes(keyframes: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for prop, points in keyframes.items():
        if not isinstance(points, list):
            continue
        kfs = []
        for pt in points:
            if not isinstance(pt, dict) or pt.get("t") is None:
                continue
            interp = str(pt.get("interp") or "linear")
            kfs.append({
                "t": round(_as_float(pt.get("t")), TIME_NDIGITS),
                "value": pt.get("value"),
                "interp": interp if interp in INTERP_KINDS else "linear",
            })
        kfs.sort(key=lambda k: k["t"])
        out[str(prop)] = kfs
    return out


def _normalize_time_remap(remap: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a time-remap (speed-ramp) spec, or ``None`` if it has no points.

    A remap is ``{"keyframes": [{"t": output_seconds, "value": source_seconds,
    "interp": "linear"|"hold"}, ...], "extrapolate": "hold"|"loop"|"pingpong"}``.
    ``t`` is a time on the layer's *output* timeline (layer-local seconds);
    ``value`` is the *source* time it samples. Keyframes are sorted by ``t``;
    floats are rounded to ``TIME_NDIGITS`` so the doc round-trips byte-stably.
    """
    points = remap.get("keyframes")
    if not isinstance(points, list):
        return None
    kfs: list[dict[str, Any]] = []
    for pt in points:
        if not isinstance(pt, dict) or pt.get("t") is None or pt.get("value") is None:
            continue
        interp = str(pt.get("interp") or "linear")
        kfs.append({
            "t": round(_as_float(pt.get("t")), TIME_NDIGITS),
            "value": round(_as_float(pt.get("value")), TIME_NDIGITS),
            "interp": interp if interp in TIME_REMAP_INTERP else "linear",
        })
    if not kfs:
        return None
    kfs.sort(key=lambda k: k["t"])
    extrapolate = str(remap.get("extrapolate") or "hold")
    if extrapolate not in TIME_REMAP_EXTRAPOLATE:
        extrapolate = "hold"
    return {"keyframes": kfs, "extrapolate": extrapolate}


def eval_time_remap(remap: dict[str, Any], out_seconds: float) -> float:
    """Evaluate a normalised time-remap curve: output seconds -> source seconds.

    Between keyframes the curve interpolates by the *left* keyframe's ``interp``
    (``hold`` freezes the source time, ``linear`` ramps it). Outside the keyframe
    span the ``extrapolate`` policy applies over the output-time domain:

    * ``hold``     — clamp to the nearest endpoint's source value (a freeze).
    * ``loop``     — wrap the output time into ``[t0, tN]`` and re-evaluate.
    * ``pingpong`` — reflect the output time so the curve plays forward/back.

    A single keyframe (or a zero-length span) degenerates to a constant source
    time. The result is plain seconds; the caller quantises to a frame.
    """
    kfs = remap.get("keyframes") or []
    n = len(kfs)
    if n == 0:
        return float(out_seconds)
    if n == 1:
        return float(kfs[0]["value"])

    t0 = float(kfs[0]["t"])
    tn = float(kfs[-1]["t"])
    span = tn - t0
    t = float(out_seconds)

    if span <= 0:
        return float(kfs[0]["value"])

    extrapolate = str(remap.get("extrapolate") or "hold")
    if t < t0 or t > tn:
        if extrapolate == "loop":
            t = t0 + ((t - t0) % span)
        elif extrapolate == "pingpong":
            phase = (t - t0) % (2.0 * span)
            t = t0 + (phase if phase <= span else 2.0 * span - phase)
        else:  # hold: clamp to the matching endpoint
            return float(kfs[0]["value"] if t < t0 else kfs[-1]["value"])

    # Locate the segment [a, b] with a.t <= t <= b.t.
    for i in range(n - 1):
        a, b = kfs[i], kfs[i + 1]
        ta, tb = float(a["t"]), float(b["t"])
        if ta <= t <= tb:
            if str(a.get("interp")) == "hold" or tb <= ta:
                return float(a["value"])
            frac = (t - ta) / (tb - ta)
            return float(a["value"]) + frac * (float(b["value"]) - float(a["value"]))
    # Float fall-through (t == tn within rounding): last keyframe.
    return float(kfs[-1]["value"])


# ── tree traversal & lookup ─────────────────────────────────────────────


def walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Depth-first iterator over a layer and all descendants (self first)."""
    yield node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from walk(child)


def iter_layers(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every layer in the doc except the synthetic ``root`` composition."""
    root = doc.get("root") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return
    for child in root.get("children") or []:
        if isinstance(child, dict):
            yield from walk(child)


def find_layer(doc_or_node: dict[str, Any], layer_id: str) -> dict[str, Any] | None:
    """Find a layer by id anywhere in the tree (root included)."""
    root = _root_of(doc_or_node)
    target = str(layer_id)
    for node in walk(root):
        if str(node.get("id")) == target:
            return node
    return None


def find_parent(doc_or_node: dict[str, Any], layer_id: str) -> dict[str, Any] | None:
    """Return the parent layer that directly contains ``layer_id`` (or None)."""
    root = _root_of(doc_or_node)
    target = str(layer_id)
    for node in walk(root):
        for child in node.get("children") or []:
            if isinstance(child, dict) and str(child.get("id")) == target:
                return node
    return None


def locate(doc_or_node: dict[str, Any], layer_id: str) -> tuple[dict[str, Any], int] | None:
    """Return ``(parent, index)`` for ``layer_id``, or ``None`` if not found.

    The single primitive every structural op needs: it pins exactly where a
    layer sits so the op can splice it out / in without re-scanning.
    """
    root = _root_of(doc_or_node)
    target = str(layer_id)
    for node in walk(root):
        children = node.get("children") or []
        for index, child in enumerate(children):
            if isinstance(child, dict) and str(child.get("id")) == target:
                return node, index
    return None


def doc_duration(doc: dict[str, Any]) -> float:
    """Total timeline length = furthest child end on the root composition."""
    root = doc.get("root") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return 0.0
    return _composition_extent(root)


# ── internal helpers ────────────────────────────────────────────────────


def _root_of(doc_or_node: dict[str, Any]) -> dict[str, Any]:
    if isinstance(doc_or_node, dict) and isinstance(doc_or_node.get("root"), dict):
        return doc_or_node["root"]
    return doc_or_node if isinstance(doc_or_node, dict) else {}


def _composition_extent(comp: dict[str, Any]) -> float:
    end = 0.0
    for child in comp.get("children") or []:
        if not isinstance(child, dict):
            continue
        end = max(end, _as_float(child.get("start")) + _as_float(child.get("duration")))
    return round(end, TIME_NDIGITS)


def _collect_ids(node: dict[str, Any]) -> set[str]:
    return {str(n.get("id")) for n in walk(node) if n.get("id")}


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _id_prefix(layer_type: str) -> str:
    return {
        "composition": "comp",
        "adjustment": "adj",
        "text": "text",
        "audio": "audio",
        "shape": "shape",
        "gradient": "gradient",
    }.get(layer_type, "layer")


def _default_name(layer_type: str) -> str:
    return {
        "composition": "Composition",
        "adjustment": "Adjustment",
        "text": "Text",
        "audio": "Audio",
        "shape": "Shape",
        "gradient": "Gradient",
        "image": "Image",
        "video": "Video",
        "solid": "Solid",
        "null": "Null",
        "sticker": "Sticker",
    }.get(layer_type, layer_type.title())
