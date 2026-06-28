"""Pixel-level tests for the ``gradient`` layer (linear + radial).

SHARED LAYER SCHEMA CONTRACT exercised here (resolver + ops must agree):
  {"type": "gradient", "props": {
      "mode": "linear" | "radial",
      "stops": [[pos0..1, "#RRGGBB"], ...],
      "angle": <deg, linear, 0 = left->right, 90 = top->bottom>,
      "center": [cx0..1, cy0..1] (radial),
      "radius": <0..1 frac of canvas> (radial)}}

All coordinates normalised to canvas [0, 1]; output is float32 (H, W, 4).
"""
from __future__ import annotations

import numpy as np

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack
from lumenframe.resolve import default_resolver


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def add_gradient(doc, lid, props, *, start=0.0, duration=1.0):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "gradient",
        "start": start, "duration": duration, "props": props,
    }))


# ── output shape / dtype ────────────────────────────────────────────────────


def test_gradient_output_is_canvas_sized_rgba_float():
    doc = empty_doc(width=64, height=48, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "linear", "angle": 90,
        "stops": [[0.0, "#000000"], [1.0, "#FFFFFF"]],
    })
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert frame.shape == (48, 64, 4)
    assert frame.dtype == np.float32
    assert 0.0 <= float(frame.min()) and float(frame.max()) <= 1.0


# ── linear: vertical (angle 90, top->bottom) ────────────────────────────────


def test_linear_vertical_top_differs_from_bottom_with_mid_ramp():
    """angle=90 -> black at top, white at bottom, ~0.5 grey at the middle."""
    doc = empty_doc(width=64, height=48, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "linear", "angle": 90,
        "stops": [[0.0, "#000000"], [1.0, "#FFFFFF"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    col = 32
    top = float(f[0, col, 0])
    bottom = float(f[-1, col, 0])
    mid = float(f[24, col, 0])

    # Top dark, bottom bright, clearly different.
    assert top < 0.05
    assert bottom > 0.95
    assert bottom - top > 0.9
    # Monotonic mid ramp: ~ row/(H-1) at the centre row (row 24 of 48 -> ~0.51).
    assert abs(mid - 24.0 / 47.0) < 0.06
    # Horizontal invariance: a row's value is constant left->right.
    assert abs(float(f[24, 0, 0]) - float(f[24, 63, 0])) < 1e-4


# ── linear: horizontal (angle 0, left->right) ───────────────────────────────


def test_linear_horizontal_left_differs_from_right():
    """angle=0 -> black at left, white at right; rows are identical."""
    doc = empty_doc(width=64, height=48, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "linear", "angle": 0,
        "stops": [[0.0, "#000000"], [1.0, "#FFFFFF"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    row = 24
    left = float(f[row, 0, 0])
    right = float(f[row, -1, 0])
    mid = float(f[row, 32, 0])

    assert left < 0.05
    assert right > 0.95
    assert right - left > 0.9
    assert abs(mid - 32.0 / 63.0) < 0.06
    # Vertical invariance: top row == bottom row.
    assert abs(float(f[0, 32, 0]) - float(f[47, 32, 0])) < 1e-4


# ── linear: multi-stop ramp ─────────────────────────────────────────────────


def test_linear_multistop_hits_middle_stop_colour():
    """A 3-stop ramp (red->green->blue) reads green near the 0.5 stop."""
    doc = empty_doc(width=100, height=20, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "linear", "angle": 0,
        "stops": [[0.0, "#FF0000"], [0.5, "#00FF00"], [1.0, "#0000FF"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    row = 10
    # Far left ~ red.
    lr, lg, lb = (float(f[row, 1, 0]), float(f[row, 1, 1]), float(f[row, 1, 2]))
    assert lr > 0.9 and lg < 0.1 and lb < 0.1
    # Middle column (col 50 of 100) ~ green.
    mr, mg, mb = (float(f[row, 50, 0]), float(f[row, 50, 1]), float(f[row, 50, 2]))
    assert mg > 0.9 and mr < 0.1 and mb < 0.1
    # Far right ~ blue.
    rr, rg, rb = (float(f[row, 98, 0]), float(f[row, 98, 1]), float(f[row, 98, 2]))
    assert rb > 0.9 and rr < 0.1 and rg < 0.1


# ── radial: centre stop at centre, edge stop at corner ──────────────────────


def test_radial_centre_colour_at_centre_edge_colour_at_corner():
    """radial red->blue: centre reads red, far corner reads blue."""
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "radial", "center": [0.5, 0.5], "radius": 0.7,
        "stops": [[0.0, "#FF0000"], [1.0, "#0000FF"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)

    # Centre pixel ~ first stop (red).
    cr, cg, cb = (float(f[32, 32, 0]), float(f[32, 32, 1]), float(f[32, 32, 2]))
    assert cr > 0.9 and cb < 0.1
    # Far corner (0,0) is past the radius -> clamped to last stop (blue).
    er, eg, eb = (float(f[0, 0, 0]), float(f[0, 0, 1]), float(f[0, 0, 2]))
    assert eb > 0.9 and er < 0.1
    # Radial symmetry: the four corners read the same colour.
    for (yy, xx) in [(0, 63), (63, 0), (63, 63)]:
        assert abs(float(f[yy, xx, 2]) - eb) < 0.05


def test_radial_is_monotonic_outward():
    """Distance from centre -> colour t increases monotonically outward."""
    doc = empty_doc(width=64, height=64, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "radial", "center": [0.5, 0.5], "radius": 0.9,
        "stops": [[0.0, "#000000"], [1.0, "#FFFFFF"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # Sample along a ray from centre to the right edge: brightness rises.
    vals = [float(f[32, c, 0]) for c in range(32, 64, 4)]
    assert vals[0] < vals[-1]
    for a, b in zip(vals, vals[1:]):
        assert b >= a - 1e-3  # non-decreasing


# ── robustness: absent / malformed props must not crash ─────────────────────


def test_gradient_absent_props_does_not_crash():
    doc = empty_doc(width=32, height=32, fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "g", "type": "gradient", "duration": 1.0,
    }))
    # Must compile + render without raising (default black->white ramp).
    f = compile_to_layer_stack(doc).render_frame(0)
    assert f.shape == (32, 32, 4)
    assert f.dtype == np.float32


def test_gradient_unknown_mode_and_bad_stops_does_not_crash():
    doc = empty_doc(width=32, height=32, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "spiral",  # unknown -> degrades to linear
        "stops": "not-a-list",
        "angle": "oops",
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    assert f.shape == (32, 32, 4)


def test_gradient_single_stop_is_flat_colour():
    doc = empty_doc(width=16, height=16, fps=10)
    doc = add_gradient(doc, "g", {
        "mode": "linear", "angle": 45,
        "stops": [[0.3, "#123456"]],
    })
    f = compile_to_layer_stack(doc).render_frame(0)
    # A single stop fills the canvas with one flat colour.
    expect = (0x12 / 255.0, 0x34 / 255.0, 0x56 / 255.0)
    assert abs(float(f[0, 0, 0]) - expect[0]) < 0.01
    assert abs(float(f[8, 8, 1]) - expect[1]) < 0.01
    assert abs(float(f[15, 15, 2]) - expect[2]) < 0.01
    assert np.allclose(f, f[0, 0], atol=0.01)


# ── resolver is wired into the default dispatch ─────────────────────────────


def test_gradient_resolved_by_default_resolver_dispatch():
    from lumenframe.compile import ResolveContext
    layer = {
        "type": "gradient",
        "props": {"mode": "linear", "angle": 90,
                  "stops": [[0.0, "#000000"], [1.0, "#FFFFFF"]]},
    }
    ctx = ResolveContext(width=8, height=8, fps=10.0, total_frames=1, assets=[])
    fn = default_resolver(layer, ctx)
    assert fn is not None
    frame = fn(0)
    assert frame.shape == (8, 8, 4)
    assert float(frame[0, 4, 0]) < float(frame[7, 4, 0])
