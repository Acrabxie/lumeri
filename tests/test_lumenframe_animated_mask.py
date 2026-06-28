"""Pixel-level tests for ANIMATED shape masks in the compositing core.

Part (B) of the compositing-core hardening: ``lumenframe.compile._shape_matte``
rasterises a shape mask **per frame** when its box/centre carries keyframes or an
expression, so the masked region MOVES / scales over time. A mask with no such
animation stays on the cached single-rasterise path and is byte-identical to the
prior behaviour.

The headline assertion (per the task): an ellipse whose centre ``cx`` keyframes
left → right leaves a right-side pixel masked-out (alpha 0) at frame 0 and
reveals it (alpha > 0) at a later frame. The static-mask path is checked for
byte-equality against a fresh single rasterise.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import (
    ResolveContext,
    _rasterise_shape_mask,
    _shape_matte,
    compile_to_layer_stack,
)


W = H = 64
FPS = 10


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc():
    return empty_doc(width=W, height=H, fps=FPS)


def add_solid(doc, lid, color, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": 0.0, "duration": 2.0, **fields,
    }))


def set_mask(doc, lid, mask):
    return apply_layer_patch(doc, patch({
        "op": "set_mask", "layer_id": lid, "mask": mask,
    }))


def ctx():
    return ResolveContext(W, H, float(FPS), 21, [])


# A right-side probe pixel: outside an ellipse centred at cx=0.25, inside one
# centred at cx=0.75 (rx ~= 0.15 of the canvas).
PROBE_X = int(round(0.75 * W))   # 48
PROBE_Y = H // 2                 # 32


# ── direct _shape_matte: keyframed centre moves the alpha ─────────────────────


def test_shape_matte_keyframed_cx_moves_alpha():
    mask = {
        "kind": "shape",
        "shape": {
            "type": "ellipse", "cx": 0.25, "cy": 0.5, "rx": 0.15, "ry": 0.15,
            "keyframes": {"cx": [
                {"t": 0.0, "value": 0.25},
                {"t": 1.0, "value": 0.75},  # 1.0s * 10fps = frame 10
            ]},
        },
    }
    matte = _shape_matte(mask, ctx())
    early = matte(0)
    late = matte(10)

    assert early.shape == (H, W)
    # The right-side probe is masked-out early, revealed once the ellipse arrives.
    assert early[PROBE_Y, PROBE_X] == pytest.approx(0.0, abs=1e-6)
    assert late[PROBE_Y, PROBE_X] > 0.5
    # Symmetrically, the original left position is covered early, cleared late.
    left_x = int(round(0.25 * W))
    assert early[PROBE_Y, left_x] > 0.5
    assert late[PROBE_Y, left_x] == pytest.approx(0.0, abs=1e-6)


def test_shape_matte_keyframes_at_top_level_also_animate():
    # Animation data placed directly on the mask dict (not nested in shape) also
    # drives per-frame rasterisation — handy for direct callers.
    mask = {
        "kind": "shape",
        "shape": {"type": "ellipse", "cx": 0.25, "cy": 0.5, "rx": 0.15, "ry": 0.15},
        "keyframes": {"cx": [
            {"t": 0.0, "value": 0.25},
            {"t": 1.0, "value": 0.75},
        ]},
    }
    matte = _shape_matte(mask, ctx())
    assert matte(0)[PROBE_Y, PROBE_X] == pytest.approx(0.0, abs=1e-6)
    assert matte(10)[PROBE_Y, PROBE_X] > 0.5


def test_shape_matte_expression_animates_alpha():
    # cx expression sweeps with time: cx = 0.25 + 0.5 * time  (time in seconds).
    mask = {
        "kind": "shape",
        "shape": {
            "type": "ellipse", "cx": 0.25, "cy": 0.5, "rx": 0.15, "ry": 0.15,
            "expression": {"cx": "0.25 + 0.5 * time"},
        },
    }
    matte = _shape_matte(mask, ctx())
    # frame 0 -> time 0 -> cx 0.25 (probe outside); frame 10 -> time 1.0 -> cx 0.75.
    assert matte(0)[PROBE_Y, PROBE_X] == pytest.approx(0.0, abs=1e-6)
    assert matte(10)[PROBE_Y, PROBE_X] > 0.5


def test_shape_matte_keyframed_radius_scales_alpha():
    # rx grows over time: a pixel just outside the small early ellipse falls
    # inside the larger late one (mask SCALES, not just moves).
    mask = {
        "kind": "shape",
        "shape": {
            "type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.1,
            "keyframes": {
                "rx": [{"t": 0.0, "value": 0.1}, {"t": 1.0, "value": 0.45}],
                "ry": [{"t": 0.0, "value": 0.1}, {"t": 1.0, "value": 0.45}],
            },
        },
    }
    matte = _shape_matte(mask, ctx())
    # A pixel ~0.35 of the way across: outside r=0.1 early, inside r=0.45 late.
    edge_x = int(round(0.85 * W))   # 0.35 right of centre (0.5)
    assert matte(0)[PROBE_Y, edge_x] == pytest.approx(0.0, abs=1e-6)
    assert matte(10)[PROBE_Y, edge_x] > 0.5


# ── static masks stay byte-identical (no keyframes / expression) ──────────────


def test_static_mask_is_byte_identical_to_single_rasterise():
    mask = {
        "kind": "shape",
        "shape": {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25},
    }
    matte = _shape_matte(mask, ctx())
    reference = _rasterise_shape_mask(mask, W, H)
    a0 = matte(0)
    a5 = matte(5)
    # Same object across frames (cached) and exactly equal to a fresh rasterise.
    assert np.array_equal(a0, reference)
    assert np.array_equal(a0, a5)


def test_static_mask_unchanged_with_irrelevant_keyframe_keys():
    # A keyframes dict with no animatable field falls back to the static path.
    mask = {
        "kind": "shape",
        "shape": {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25,
                  "keyframes": {"not_a_field": [{"t": 0.0, "value": 1.0}]}},
    }
    matte = _shape_matte(mask, ctx())
    reference = _rasterise_shape_mask(
        {"kind": "shape",
         "shape": {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25}},
        W, H,
    )
    assert np.array_equal(matte(0), reference)


# ── end-to-end through compile_to_layer_stack ─────────────────────────────────


def test_animated_mask_changes_rendered_region_between_frames():
    # green solid masked by an ellipse whose centre sweeps left -> right.
    doc = add_solid(base_doc(), "green", "#00FF00")
    shape = {
        "type": "ellipse", "cx": 0.25, "cy": 0.5, "rx": 0.15, "ry": 0.15,
        "keyframes": {"cx": [
            {"t": 0.0, "value": 0.25},
            {"t": 1.0, "value": 0.75},
        ]},
    }
    doc = set_mask(doc, "green", {"kind": "shape", "shape": shape})
    stack = compile_to_layer_stack(doc)

    f0 = stack.render_frame(0)
    f10 = stack.render_frame(10)

    # The right-side pixel: cleared (transparent) at frame 0, opaque green later.
    assert f0[PROBE_Y, PROBE_X, 3] == pytest.approx(0.0, abs=1e-6)
    assert f10[PROBE_Y, PROBE_X, 3] > 0.5
    assert f10[PROBE_Y, PROBE_X, 1] > 0.5   # green channel present once revealed
    # And the regions genuinely differ between the two frames.
    assert not np.allclose(f0[..., 3], f10[..., 3])


def test_animated_mask_survives_doc_normalisation_roundtrip():
    # Confirm the animation data is preserved by normalize_doc (it lives inside
    # the shape sub-dict, which the model copies verbatim).
    doc = add_solid(base_doc(), "green", "#00FF00")
    shape = {
        "type": "ellipse", "cx": 0.25, "cy": 0.5, "rx": 0.15, "ry": 0.15,
        "keyframes": {"cx": [{"t": 0.0, "value": 0.25}, {"t": 1.0, "value": 0.75}]},
    }
    doc = set_mask(doc, "green", {"kind": "shape", "shape": shape})
    persisted = doc["root"]["children"][0]["mask"]["shape"]["keyframes"]["cx"]
    assert [p["value"] for p in persisted] == [0.25, 0.75]


def test_static_mask_render_unchanged_end_to_end():
    # A non-animated shape mask renders the same region at every frame.
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = set_mask(doc, "green", {
        "kind": "shape",
        "shape": {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25},
    })
    stack = compile_to_layer_stack(doc)
    assert np.array_equal(stack.render_frame(0)[..., 3], stack.render_frame(10)[..., 3])
