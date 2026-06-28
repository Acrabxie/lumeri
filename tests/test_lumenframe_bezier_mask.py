"""Pixel-level tests for BEZIER-PATH shape masks in the compositing core.

Builds on the animated-mask machinery (``lumenframe.compile._shape_matte``):
when a shape mask carries a ``path`` spec, the mask **centre** is driven along
that path as a function of local time ``t in [0, 1]`` while the shape itself
(``rx/ry/type/feather/invert``) is preserved. ``"linear"`` walks a piecewise
polyline through the points; ``"bezier"`` treats the points as a cubic Bezier
control polygon evaluated with De Casteljau, so the centre is pulled off the
straight chord toward the interior control points.

Headline assertions (per the task):
  * A small ellipse on a left->right path: at ``t=0`` a LEFT probe pixel is
    visible and a RIGHT probe is masked-out; near ``t=1`` it reverses.
  * A bezier path actually CURVES: the centre at ``t=0.5`` is off the straight
    chord by the control-point pull (probed via where the mask covers).
  * Static masks AND existing ``cx``/``cy``-keyframe masks stay byte-identical
    (no ``path`` => the old code path is taken verbatim).
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import (
    ResolveContext,
    _path_point,
    _rasterise_shape_mask,
    _shape_matte,
    _shape_path,
    compile_to_layer_stack,
)


W = H = 64
FPS = 10

# Path runs the centre from x=0.2 to x=0.8 at a constant y=0.5.
PATH_X0, PATH_X1 = 0.2, 0.8
PATH_Y = 0.5

# Probe columns at the two ends of the chord, on the chord's row.
LEFT_X = int(round(PATH_X0 * W))    # 13
RIGHT_X = int(round(PATH_X1 * W))   # 51
MID_Y = H // 2                      # 32


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc():
    return empty_doc(width=W, height=H, fps=FPS)


def add_solid(doc, lid, color, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": 0.0, "duration": 1.0, **fields,
    }))


def set_mask(doc, lid, mask):
    return apply_layer_patch(doc, patch({
        "op": "set_mask", "layer_id": lid, "mask": mask,
    }))


def ctx():
    # total_frames generous; the path's own duration drives local time.
    return ResolveContext(W, H, float(FPS), 20, [])


# ── pure path-geometry helpers ────────────────────────────────────────────────


def test_path_point_linear_walks_the_chord():
    pts = [(PATH_X0, PATH_Y), (PATH_X1, PATH_Y)]
    assert _path_point(pts, "linear", 0.0) == pytest.approx((0.2, 0.5))
    assert _path_point(pts, "linear", 0.5) == pytest.approx((0.5, 0.5))
    assert _path_point(pts, "linear", 1.0) == pytest.approx((0.8, 0.5))


def test_path_point_linear_multi_segment():
    # Three points => two equal segments; t=0.5 lands exactly on the middle pt.
    pts = [(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)]
    assert _path_point(pts, "linear", 0.0) == pytest.approx((0.0, 0.0))
    assert _path_point(pts, "linear", 0.5) == pytest.approx((0.5, 1.0))
    assert _path_point(pts, "linear", 1.0) == pytest.approx((1.0, 0.0))
    # quarter-way along the first segment
    assert _path_point(pts, "linear", 0.25) == pytest.approx((0.25, 0.5))


def test_path_point_bezier_de_casteljau_is_curved():
    # Cubic with both interior controls pulled UP to y=0.1; endpoints at y=0.5.
    # Analytic cubic value at t=0.5:
    #   y = (1-t)^3*y0 + 3(1-t)^2 t*y1 + 3(1-t)t^2*y2 + t^3*y3
    #     = 0.125*0.5 + 0.375*0.1 + 0.375*0.1 + 0.125*0.5 = 0.2
    bez = [(0.2, 0.5), (0.5, 0.1), (0.5, 0.1), (0.8, 0.5)]
    p0 = _path_point(bez, "bezier", 0.0)
    pm = _path_point(bez, "bezier", 0.5)
    p1 = _path_point(bez, "bezier", 1.0)
    assert p0 == pytest.approx((0.2, 0.5))   # passes through first control point
    assert p1 == pytest.approx((0.8, 0.5))   # passes through last control point
    # x is symmetric => 0.5 at the midpoint; y is pulled toward the controls.
    assert pm[0] == pytest.approx(0.5, abs=1e-9)
    assert pm[1] == pytest.approx(0.2, abs=1e-9)
    # The defining property: the curve is OFF the straight chord at the midpoint.
    chord_mid_y = (bez[0][1] + bez[-1][1]) / 2.0   # 0.5
    pull = chord_mid_y - pm[1]                       # how far the control pulls it
    assert pull == pytest.approx(0.3, abs=1e-9)
    assert abs(pull) > 0.05  # genuinely curved, not a straight line


def test_path_point_two_points_bezier_is_a_line():
    # A 2-point "bezier" degenerates to the straight chord (De Casteljau on 2 pts).
    pts = [(0.2, 0.4), (0.8, 0.6)]
    assert _path_point(pts, "bezier", 0.5) == pytest.approx((0.5, 0.5))


# ── path-spec extraction (both homes, survives roundtrip) ──────────────────────


def test_shape_path_reads_nested_and_top_level():
    nested = _shape_path({"shape": {"type": "ellipse",
                                    "path": {"points": [[0.2, 0.5], [0.8, 0.5]]}}})
    assert nested is not None
    assert nested["kind"] == "linear" and nested["loop"] is False
    assert nested["points"] == [(0.2, 0.5), (0.8, 0.5)]

    top = _shape_path({"path": {"points": [[0, 0], [1, 1]], "kind": "bezier",
                                "duration": 2.0, "loop": True}})
    assert top is not None
    assert top["kind"] == "bezier" and top["duration"] == 2.0 and top["loop"] is True


def test_shape_path_none_without_a_usable_spec():
    assert _shape_path({"shape": {"type": "ellipse", "cx": 0.5}}) is None
    # fewer than two points is not a usable path
    assert _shape_path({"path": {"points": [[0.5, 0.5]]}}) is None
    assert _shape_path({"path": {"points": "nope"}}) is None


# ── direct _shape_matte: the mask travels the path ─────────────────────────────


def _ellipse_path_mask(kind, points, **extra):
    return {
        "kind": "shape",
        "shape": {
            "type": "ellipse", "rx": 0.1, "ry": 0.1, "cx": 0.5, "cy": 0.5,
            "path": {"points": points, "kind": kind, **extra},
        },
    }


def test_linear_path_centre_travels_left_to_right():
    mask = _ellipse_path_mask("linear", [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]])
    matte = _shape_matte(mask, ctx(), layer_frames=10)  # 1s @ 10fps
    early = matte(0)
    late = matte(10)

    assert early.shape == (H, W)
    # t=0: ellipse at the LEFT end => left probe visible, right probe masked.
    assert early[MID_Y, LEFT_X] > 0.5
    assert early[MID_Y, RIGHT_X] == pytest.approx(0.0, abs=1e-6)
    # near t=1: it reverses.
    assert late[MID_Y, LEFT_X] == pytest.approx(0.0, abs=1e-6)
    assert late[MID_Y, RIGHT_X] > 0.5
    # And the alpha genuinely differs frame-to-frame.
    assert not np.array_equal(early, late)


def test_linear_path_centre_at_midframe_is_chord_midpoint():
    mask = _ellipse_path_mask("linear", [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]])
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    mid = matte(5)  # t = 0.5
    mid_x = int(round(0.5 * W))
    # Centre sits at the chord midpoint (x=0.5): both ends are now uncovered,
    # the middle is covered.
    assert mid[MID_Y, mid_x] > 0.5
    assert mid[MID_Y, LEFT_X] == pytest.approx(0.0, abs=1e-6)
    assert mid[MID_Y, RIGHT_X] == pytest.approx(0.0, abs=1e-6)


def test_bezier_path_curves_off_the_straight_chord():
    # Same endpoints as the linear chord, but controls pull the centre UP
    # (to y=0.1) so at t=0.5 the centre is well above the chord's y=0.5 row.
    bez_pts = [[PATH_X0, PATH_Y], [0.5, 0.1], [0.5, 0.1], [PATH_X1, PATH_Y]]
    mask = _ellipse_path_mask("bezier", bez_pts)
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    mid = matte(5)  # t = 0.5

    mid_x = int(round(0.5 * W))
    chord_y = int(round(0.5 * H))   # where a straight chord's centre would be
    curve_y = int(round(0.2 * H))   # analytic bezier centre y at t=0.5

    # The mask centre is NOT on the chord row (the straight-line position is now
    # uncovered) but IS on the pulled-up curve row.
    assert mid[chord_y, mid_x] == pytest.approx(0.0, abs=1e-6)
    assert mid[curve_y, mid_x] > 0.5

    # Cross-check against a straight-linear path with identical endpoints: its
    # midpoint sits on the chord row, proving the bezier genuinely diverged.
    lin_mask = _ellipse_path_mask("linear", [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]])
    lin_mid = _shape_matte(lin_mask, ctx(), layer_frames=10)(5)
    assert lin_mid[chord_y, mid_x] > 0.5
    assert lin_mid[curve_y, mid_x] == pytest.approx(0.0, abs=1e-6)


def test_path_duration_overrides_layer_span():
    # An explicit 2s path duration means frame 10 (=1s) is only t=0.5, so the
    # centre is at the chord midpoint, not the right end.
    mask = _ellipse_path_mask("linear", [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]],
                              duration=2.0)
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    half = matte(10)  # 1.0s of a 2.0s path => t=0.5
    mid_x = int(round(0.5 * W))
    assert half[MID_Y, mid_x] > 0.5
    assert half[MID_Y, RIGHT_X] == pytest.approx(0.0, abs=1e-6)


def test_path_loop_is_periodic():
    # A looping path repeats with period = path_frames-1 (=9 here): frame N and
    # frame N+9 land on the same centre, and the loop boundary wraps t->0.
    mask = _ellipse_path_mask("linear", [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]],
                              loop=True)
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    # frame 0 and frame 9 (one full cycle) both wrap to t=0 -> LEFT end covered.
    assert matte(0)[MID_Y, LEFT_X] > 0.5
    assert matte(9)[MID_Y, LEFT_X] > 0.5
    # A frame mid-cycle and the same offset one cycle later are byte-identical.
    assert np.array_equal(matte(4), matte(13))
    # And mid-cycle the centre has genuinely moved off the left end.
    assert matte(4)[MID_Y, LEFT_X] == pytest.approx(0.0, abs=1e-6)


def test_path_preserves_shape_size_and_invert():
    # rx/ry and invert are honoured while the centre rides the path.
    mask = {
        "kind": "shape",
        "invert": True,
        "shape": {
            "type": "ellipse", "rx": 0.1, "ry": 0.1, "cx": 0.5, "cy": 0.5,
            "path": {"points": [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]],
                     "kind": "linear"},
        },
    }
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    a0 = matte(0)
    # invert flips coverage: at t=0 the ellipse sits at the LEFT, so the left
    # probe is now the HOLE (alpha 0) and the right is opaque (alpha 1).
    assert a0[MID_Y, LEFT_X] == pytest.approx(0.0, abs=1e-6)
    assert a0[MID_Y, RIGHT_X] == pytest.approx(1.0, abs=1e-6)


# ── static / keyframe masks stay byte-identical (no path => old path taken) ────


def test_static_mask_byte_identical_without_path():
    mask = {
        "kind": "shape",
        "shape": {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25},
    }
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    reference = _rasterise_shape_mask(mask, W, H)
    a0, a5 = matte(0), matte(5)
    assert np.array_equal(a0, reference)
    assert np.array_equal(a0, a5)        # same cached object across frames


def test_cx_keyframe_mask_unaffected_by_path_feature():
    # An existing cx-keyframe mask (no path) animates exactly as before.
    mask = {
        "kind": "shape",
        "shape": {
            "type": "ellipse", "cx": 0.2, "cy": 0.5, "rx": 0.1, "ry": 0.1,
            "keyframes": {"cx": [{"t": 0.0, "value": 0.2},
                                 {"t": 1.0, "value": 0.8}]},
        },
    }
    matte = _shape_matte(mask, ctx(), layer_frames=10)
    early, late = matte(0), matte(10)
    assert early[MID_Y, LEFT_X] > 0.5
    assert early[MID_Y, RIGHT_X] == pytest.approx(0.0, abs=1e-6)
    assert late[MID_Y, RIGHT_X] > 0.5
    assert late[MID_Y, LEFT_X] == pytest.approx(0.0, abs=1e-6)


# ── end-to-end through compile_to_layer_stack + render_frame ───────────────────


def test_path_mask_renders_through_compile_and_roundtrips():
    doc = add_solid(base_doc(), "green", "#00FF00")
    shape = {
        "type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.1,
        "path": {"points": [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]], "kind": "linear"},
    }
    doc = set_mask(doc, "green", {"kind": "shape", "shape": shape})

    # The path spec survives normalize_doc (it lives inside the shape sub-dict).
    persisted = doc["root"]["children"][0]["mask"]["shape"]["path"]
    assert persisted["points"] == [[PATH_X0, PATH_Y], [PATH_X1, PATH_Y]]
    assert persisted["kind"] == "linear"

    stack = compile_to_layer_stack(doc)
    f0 = stack.render_frame(0)
    f9 = stack.render_frame(9)  # layer duration 1s @ 10fps => last frame, t=1

    # Left probe: opaque green at t=0, transparent at t=1.
    assert f0[MID_Y, LEFT_X, 3] > 0.5
    assert f0[MID_Y, LEFT_X, 1] > 0.5  # green channel present where covered
    assert f9[MID_Y, LEFT_X, 3] == pytest.approx(0.0, abs=1e-6)
    # Right probe: the mirror image.
    assert f0[MID_Y, RIGHT_X, 3] == pytest.approx(0.0, abs=1e-6)
    assert f9[MID_Y, RIGHT_X, 3] > 0.5
    # The two frames genuinely differ.
    assert not np.allclose(f0[..., 3], f9[..., 3])


def test_bezier_path_mask_renders_curved_through_compile():
    doc = add_solid(base_doc(), "green", "#00FF00")
    bez_pts = [[PATH_X0, PATH_Y], [0.5, 0.1], [0.5, 0.1], [PATH_X1, PATH_Y]]
    shape = {
        "type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.1,
        "path": {"points": bez_pts, "kind": "bezier"},
    }
    doc = set_mask(doc, "green", {"kind": "shape", "shape": shape})
    stack = compile_to_layer_stack(doc)
    f5 = stack.render_frame(5)  # t = 0.5

    mid_x = int(round(0.5 * W))
    chord_y = int(round(0.5 * H))
    curve_y = int(round(0.2 * H))
    # The covered green region at the midpoint sits on the pulled-up curve row,
    # NOT on the straight chord row.
    assert f5[chord_y, mid_x, 3] == pytest.approx(0.0, abs=1e-6)
    assert f5[curve_y, mid_x, 3] > 0.5
    assert f5[curve_y, mid_x, 1] > 0.5
