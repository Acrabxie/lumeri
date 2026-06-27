"""Shape-mask rasterisation — rectangle / ellipse / polygon vector masks.

Real-render pixel assertions through ``compile_to_layer_stack`` plus a few
direct checks on the rasteriser. A shape mask is drawn in normalised canvas
coordinates and bakes ``feather`` + ``invert`` into the alpha the backend
multiplies onto the (transformed) layer frame.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import _rasterise_shape_mask, compile_to_layer_stack


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=80, h=80, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": 0.0, "duration": 1.0, **fields,
    }))


def set_shape_mask(doc, lid, shape, *, invert=False, feather=0.0):
    return apply_layer_patch(doc, patch({
        "op": "set_mask", "layer_id": lid,
        "mask": {"kind": "shape", "shape": shape, "invert": invert, "feather": feather},
    }))


def px(frame, col, row):
    return frame[row, col]


# ── rectangle ──────────────────────────────────────────────────────────────


def test_rectangle_mask_keeps_inside_clears_outside():
    # green over red; the rect keeps green in the centre, red shows at corners.
    doc = add_solid(base_doc(), "red", "#FF0000")
    doc = add_solid(doc, "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"type": "rectangle", "x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75})
    frame = compile_to_layer_stack(doc).render_frame(0)

    centre = px(frame, 40, 40)
    assert centre[1] == pytest.approx(1.0) and centre[0] == pytest.approx(0.0)  # green wins
    corner = px(frame, 4, 4)
    assert corner[0] == pytest.approx(1.0) and corner[1] == pytest.approx(0.0)  # red shows (green masked)


def test_rectangle_mask_alpha_is_zero_outside_over_transparent():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75})
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)   # inside opaque
    assert px(frame, 4, 4)[3] == pytest.approx(0.0)     # outside fully cleared


def test_rect_accepts_rect_list_form():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"type": "rect", "rect": [0.25, 0.25, 0.75, 0.75]})
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)
    assert px(frame, 4, 4)[3] == pytest.approx(0.0)


# ── ellipse ────────────────────────────────────────────────────────────────


def test_ellipse_mask_clears_bbox_corners():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"type": "ellipse", "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0})
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)   # centre inside ellipse
    assert px(frame, 2, 2)[3] == pytest.approx(0.0)     # bbox corner outside ellipse


def test_ellipse_centre_radii_form():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"type": "circle", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25})
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)   # centre inside
    assert px(frame, 40, 4)[3] == pytest.approx(0.0)    # top edge outside r=0.25


# ── polygon ────────────────────────────────────────────────────────────────


def test_polygon_triangle_mask():
    doc = add_solid(base_doc(), "green", "#00FF00")
    tri = {"type": "polygon", "points": [[0.5, 0.05], [0.05, 0.95], [0.95, 0.95]]}
    doc = set_shape_mask(doc, "green", tri)
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 70)[3] == pytest.approx(1.0)   # inside (near bottom centre)
    assert px(frame, 4, 4)[3] == pytest.approx(0.0)     # top-left corner outside triangle


# ── invert / feather / defaults ──────────────────────────────────────────────


def test_invert_flips_coverage():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75}, invert=True)
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(0.0)   # centre now cleared
    assert px(frame, 4, 4)[3] == pytest.approx(1.0)     # corner now kept


def test_missing_shape_defaults_to_full_canvas():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "green", "mask": {"kind": "shape"}}))
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)
    assert px(frame, 2, 2)[3] == pytest.approx(1.0)     # full-canvas rect keeps everything


def test_rounded_rect_clips_corner():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0, "radius": 0.4})
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)   # centre kept
    assert px(frame, 1, 1)[3] == pytest.approx(0.0)     # rounded corner clipped away


# ── direct rasteriser ────────────────────────────────────────────────────────


def test_rasterise_rectangle_alpha_values():
    mask = {"kind": "shape", "shape": {"x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75}}
    alpha = _rasterise_shape_mask(mask, 100, 100)
    assert alpha.shape == (100, 100)
    assert alpha.dtype == np.float32
    assert alpha[50, 50] == pytest.approx(1.0)
    assert alpha[5, 5] == pytest.approx(0.0)


def test_feather_softens_edge():
    mask = {"kind": "shape", "feather": 0.05, "shape": {"x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75}}
    alpha = _rasterise_shape_mask(mask, 100, 100)
    assert alpha[50, 50] > 0.95           # well inside still solid
    assert alpha[5, 5] < 0.05             # well outside still clear
    edge = alpha[50, 25]                  # right on the left boundary
    assert 0.2 < edge < 0.8               # softened, not a hard step


def test_set_mask_op_end_to_end_shape():
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_shape_mask(doc, "green", {"type": "ellipse"})  # defaults to full-canvas ellipse
    green = doc["root"]["children"][0]
    assert green["mask"]["kind"] == "shape"
    frame = compile_to_layer_stack(doc).render_frame(0)
    assert px(frame, 40, 40)[3] == pytest.approx(1.0)   # centre of full-canvas ellipse
    assert px(frame, 1, 1)[3] == pytest.approx(0.0)     # corner outside ellipse
