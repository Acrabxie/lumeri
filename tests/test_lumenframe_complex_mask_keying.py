"""Complex mask and keying coverage for lumenframe.

Locks three production-facing promises:
* layers can carry transparent regions from direct pixel masks;
* vector masks can express multi-contour/path shapes, not only rectangles;
* keying effects write real alpha, including advanced chroma and luma keys.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import _rasterise_shape_mask, compile_to_layer_stack


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def add_solid(doc, lid, color="#00FF00", **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer",
        "id": lid,
        "type": "solid",
        "color": color,
        "start": 0.0,
        "duration": 1.0,
        **fields,
    }))


def test_inline_pixel_mask_keeps_left_half_only():
    doc = empty_doc(width=4, height=2, fps=1)
    doc = add_solid(doc, "red", "#FF0000")
    doc = add_solid(doc, "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({
        "op": "set_mask",
        "layer_id": "green",
        "mask": {
            "kind": "pixel",
            "alpha": [
                [1, 1, 0, 0],
                [1, 1, 0, 0],
            ],
        },
    }))

    frame = compile_to_layer_stack(doc).render_frame(0)
    assert frame[0, 0, 1] == pytest.approx(1.0)  # green visible on kept side
    assert frame[0, 3, 0] == pytest.approx(1.0)  # red shows through on cut side
    assert frame[0, 3, 1] == pytest.approx(0.0)


def test_pixel_mask_can_come_from_image_asset(tmp_path: Path):
    mask_path = tmp_path / "mask.png"
    rgba = np.zeros((2, 4, 4), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[:, :2, 3] = 255
    PILImage.fromarray(rgba, "RGBA").save(mask_path)

    doc = empty_doc(width=4, height=2, fps=1)
    doc["assets"].append({"id": "mask_asset", "path": str(mask_path)})
    doc = add_solid(doc, "red", "#FF0000")
    doc = add_solid(doc, "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({
        "op": "set_mask",
        "layer_id": "green",
        "mask": {"kind": "pixel", "asset_id": "mask_asset", "channel": "alpha"},
    }))

    frame = compile_to_layer_stack(doc).render_frame(0)
    assert frame[0, 1, 1] == pytest.approx(1.0)
    assert frame[0, 3, 0] == pytest.approx(1.0)
    assert frame[0, 3, 1] == pytest.approx(0.0)


def test_vector_path_mask_supports_evenodd_holes():
    mask = {
        "kind": "shape",
        "shape": {
            "type": "path",
            "fill_rule": "evenodd",
            "contours": [
                [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                [[0.35, 0.35], [0.65, 0.35], [0.65, 0.65], [0.35, 0.65]],
            ],
        },
    }

    alpha = _rasterise_shape_mask(mask, 100, 100)
    assert alpha[20, 20] == pytest.approx(1.0)
    assert alpha[50, 50] == pytest.approx(0.0)


def test_bezier_vector_mask_renders_curved_filled_region():
    mask = {
        "kind": "shape",
        "shape": {
            "type": "bezier",
            "points": [[0.1, 0.85], [0.5, 0.05], [0.9, 0.85]],
            "samples": 96,
        },
    }

    alpha = _rasterise_shape_mask(mask, 100, 100)
    assert alpha[80, 50] > 0.9
    assert alpha[5, 50] == pytest.approx(0.0)


def test_advanced_chroma_key_makes_green_transparent_and_keeps_red():
    doc = empty_doc(width=4, height=2, fps=1)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer",
        "id": "plate",
        "type": "image",
        "start": 0.0,
        "duration": 1.0,
    }, {
        "op": "add_effect",
        "layer_id": "plate",
        "type": "advanced_chroma_key",
        "params": {"key_color": "#00FF00", "similarity": 0.18, "softness": 0.05, "spill": 1.0},
    }))

    source = np.zeros((2, 4, 4), dtype=np.float32)
    source[..., 1] = 1.0
    source[..., 3] = 1.0
    source[:, 2:, :] = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)

    def resolver(layer, _ctx):
        if layer.get("id") == "plate":
            return lambda _frame: source.copy()
        return None

    frame = compile_to_layer_stack(doc, resolver=resolver).render_frame(0)
    assert frame[0, 0, 3] == pytest.approx(0.0)
    assert frame[0, 3, 3] == pytest.approx(1.0)
    assert frame[0, 3, 0] == pytest.approx(1.0)


def test_luma_key_removes_dark_pixels():
    doc = empty_doc(width=4, height=2, fps=1)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer",
        "id": "plate",
        "type": "image",
        "start": 0.0,
        "duration": 1.0,
    }, {
        "op": "add_effect",
        "layer_id": "plate",
        "type": "luma_key",
        "params": {"threshold": 0.5, "softness": 0.0, "mode": "key_dark"},
    }))

    source = np.zeros((2, 4, 4), dtype=np.float32)
    source[..., 3] = 1.0
    source[:, 2:, :3] = 1.0

    def resolver(layer, _ctx):
        if layer.get("id") == "plate":
            return lambda _frame: source.copy()
        return None

    frame = compile_to_layer_stack(doc, resolver=resolver).render_frame(0)
    assert frame[0, 0, 3] == pytest.approx(0.0)
    assert frame[0, 3, 3] == pytest.approx(1.0)
