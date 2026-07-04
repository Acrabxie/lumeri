"""Pixel-level tests for the ``shape`` layer (rect / ellipse / polygon / line).

SHARED LAYER SCHEMA CONTRACT exercised here (resolver + ops must agree):
  {"type": "shape", "props": {
      "kind": "rect" | "ellipse" | "polygon" | "line",
      "fill": "#RRGGBB" | null,
      "stroke": {"color": "#RRGGBB", "width": <px>} (optional),
      "rect": [x0, y0, x1, y1] (normalised, rect/ellipse)
        OR "cx","cy","rx","ry"
        OR "points": [[x, y], ...] (polygon/line),
      "radius": <px corner radius> (rect),
      "opacity_baked": false}}

All coordinates normalised to canvas [0, 1]; output float32 (H, W, 4) with
alpha == 0 outside the shape.
"""
from __future__ import annotations

import numpy as np

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack
from lumenframe.resolve import default_resolver


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def add_shape(doc, lid, props, *, start=0.0, duration=1.0):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "shape",
        "start": start, "duration": duration, "props": props,
    }))


# ── output shape / dtype ────────────────────────────────────────────────────


def test_shape_output_is_canvas_sized_rgba_float():
    doc = empty_doc(width=64, height=48, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": "#00FF00", "rect": [0.25, 0.25, 0.75, 0.75],
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    assert f.shape == (48, 64, 4)
    assert f.dtype == np.float32


# ── rect: fill covers box, alpha 0 outside ──────────────────────────────────


def test_rect_fill_covers_box_and_alpha_zero_outside():
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": "#00FF00", "rect": [0.25, 0.25, 0.75, 0.75],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    # Centre is opaque green.
    cr, cg, cb, ca = [float(x) for x in f[32, 32]]
    assert cg > 0.95 and cr < 0.05 and cb < 0.05 and ca > 0.95

    # Well inside the box (e.g. col 20, row 20 -> 0.31,0.31) is filled.
    assert float(f[20, 20, 3]) > 0.95
    assert float(f[44, 44, 3]) > 0.95

    # Far outside the box (corner) is fully transparent.
    assert float(f[2, 2, 3]) == 0.0
    assert float(f[61, 61, 3]) == 0.0
    # Outside RGB carries no premultiplied colour leakage.
    assert float(f[2, 2, 1]) == 0.0


def test_rect_accepts_cx_cy_rx_ry_form():
    doc = empty_doc(width=80, height=80, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": "#0000FF",
        "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25,
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Box spans x,y in [0.25,0.75] -> px [20,60]. Centre filled blue.
    assert float(f[40, 40, 2]) > 0.95
    assert float(f[40, 40, 3]) > 0.95
    # Corner outside.
    assert float(f[5, 5, 3]) == 0.0


def test_rect_corner_radius_rounds_the_corners():
    """A large corner radius makes the rect's literal corner transparent."""
    doc = empty_doc(width=80, height=80, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": "#FFFFFF",
        "rect": [0.1, 0.1, 0.9, 0.9], "radius": 24,
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Box px corner ~ (8, 8); with radius 24 that corner is carved away.
    assert float(f[9, 9, 3]) < 0.5
    # Centre still solid.
    assert float(f[40, 40, 3]) > 0.95
    # Mid-edge (x at left edge ~8, y centred) stays filled (radius only eats corners).
    assert float(f[40, 9, 3]) > 0.9


# ── ellipse: round, bbox corner transparent ─────────────────────────────────


def test_ellipse_is_round_bbox_corner_transparent():
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "ellipse", "fill": "#00FF00", "rect": [0.1, 0.1, 0.9, 0.9],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    # Centre opaque.
    assert float(f[32, 32, 3]) > 0.95
    # bbox spans px [6.4, 57.6]; its corner (7, 7) lies outside the ellipse.
    assert float(f[7, 7, 3]) < 0.1
    # The four bbox corners are all transparent (truly round, not a rectangle).
    assert float(f[7, 56, 3]) < 0.1
    assert float(f[56, 7, 3]) < 0.1
    assert float(f[56, 56, 3]) < 0.1
    # A point on the horizontal axis through centre, near the edge, is filled.
    assert float(f[32, 55, 3]) > 0.5


# ── stroke adds an edge of the stroke colour ────────────────────────────────


def test_rect_stroke_adds_edge_of_stroke_colour():
    """A red stroke around a green rect paints red pixels on the border."""
    doc = empty_doc(width=80, height=80, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": "#00FF00", "rect": [0.25, 0.25, 0.75, 0.75],
        "stroke": {"color": "#FF0000", "width": 4},
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    # Box spans px [20, 60]. Interior is green.
    ir, ig, ib = (float(f[40, 40, 0]), float(f[40, 40, 1]), float(f[40, 40, 2]))
    assert ig > 0.95 and ir < 0.05 and ib < 0.05

    # Just inside the left edge of the box there's a red stroke band.
    edge = f[40, 21]
    assert float(edge[0]) > 0.9   # red high
    assert float(edge[1]) < 0.2   # green low
    assert float(edge[3]) > 0.9   # opaque

    # Top edge is also stroked red.
    top_edge = f[21, 40]
    assert float(top_edge[0]) > 0.9 and float(top_edge[1]) < 0.2


def test_ellipse_stroke_adds_edge_of_stroke_colour():
    doc = empty_doc(width=80, height=80, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "ellipse", "fill": "#0000FF", "rect": [0.2, 0.2, 0.8, 0.8],
        "stroke": {"color": "#FF0000", "width": 5},
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Centre is blue fill.
    assert float(f[40, 40, 2]) > 0.95 and float(f[40, 40, 0]) < 0.05
    # Scan the centre row outward to find the red stroke band before transparency.
    found_red = False
    for c in range(40, 80):
        r, g, b, a = [float(x) for x in f[40, c]]
        if a > 0.8 and r > 0.8 and b < 0.3:
            found_red = True
            break
    assert found_red, "expected a red stroke edge on the ellipse rim"


# ── polygon + line render visibly ───────────────────────────────────────────


def test_polygon_triangle_fills_interior_alpha_zero_outside():
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "polygon", "fill": "#FFFF00",
        "points": [[0.5, 0.1], [0.9, 0.9], [0.1, 0.9]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Interior near the base centre is filled yellow.
    assert float(f[50, 32, 3]) > 0.9
    assert float(f[50, 32, 0]) > 0.9 and float(f[50, 32, 1]) > 0.9
    # Top-left corner is outside the triangle -> transparent.
    assert float(f[5, 5, 3]) < 0.1


def test_line_renders_along_its_path():
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "line",
        "points": [[0.1, 0.5], [0.9, 0.5]],
        "stroke": {"color": "#FFFFFF", "width": 4},
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Midpoint of the horizontal line at y=32 is painted.
    assert float(f[32, 32, 3]) > 0.8
    assert float(f[32, 32, 0]) > 0.8
    # Far from the line (top) is transparent.
    assert float(f[5, 32, 3]) < 0.1


# ── robustness ──────────────────────────────────────────────────────────────


def test_shape_absent_geometry_does_not_crash():
    doc = empty_doc(width=32, height=32, fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "s", "type": "shape", "duration": 1.0,
        "props": {"kind": "rect", "fill": "#00FF00"},  # no rect/cx geometry
    }))
    # Layer resolves to nothing -> renders a transparent canvas, no exception.
    f = compile_to_layer_stack(doc).render_frame(0)
    assert f.shape == (32, 32, 4)


def test_shape_unknown_kind_does_not_crash():
    doc = empty_doc(width=32, height=32, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "blob",  # unknown -> degrades to rect
        "fill": "#00FF00", "rect": [0.2, 0.2, 0.8, 0.8],
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    assert f.shape == (32, 32, 4)
    assert float(f[16, 16, 3]) > 0.9


def test_shape_fill_null_with_stroke_only():
    doc = empty_doc(width=80, height=80, fps=10)
    doc = add_shape(doc, "s", {
        "kind": "rect", "fill": None, "rect": [0.25, 0.25, 0.75, 0.75],
        "stroke": {"color": "#FF0000", "width": 4},
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Interior has no fill -> transparent.
    assert float(f[40, 40, 3]) < 0.1
    # Edge band is red.
    assert float(f[40, 21, 0]) > 0.9 and float(f[40, 21, 3]) > 0.9


# ── resolver wired into default dispatch ────────────────────────────────────


def test_shape_resolved_by_default_resolver_dispatch():
    from lumenframe.compile import ResolveContext
    layer = {
        "type": "shape",
        "props": {"kind": "rect", "fill": "#00FF00",
                  "rect": [0.25, 0.25, 0.75, 0.75]},
    }
    ctx = ResolveContext(width=64, height=64, fps=10.0, total_frames=1, assets=[])
    fn = default_resolver(layer, ctx)
    assert fn is not None
    frame = fn(0)
    assert frame.shape == (64, 64, 4)
    assert float(frame[32, 32, 1]) > 0.9   # green centre
    assert float(frame[2, 2, 3]) == 0.0    # transparent corner
