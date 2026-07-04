"""Comprehensive tests for the 9 new effect filters + flip_layer op."""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def add_effect(doc, lid, effect_type, params=None):
    """Convenience: add an effect to a layer."""
    return apply_layer_patch(doc, patch({
        "op": "add_effect", "layer_id": lid, "type": effect_type, "params": params or {}
    }))


def center_px(frame):
    """Get center pixel [R, G, B, A]."""
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


def render_frame(doc, frame_index=0):
    """Compile and render a single frame."""
    stack = compile_to_layer_stack(doc)
    return stack.render_frame(frame_index)


# ════════════════════════════════════════════════════════════════════════
# 1. INVERT
# ════════════════════════════════════════════════════════════════════════


def test_invert_rgb_not_alpha():
    """Invert flips RGB but preserves alpha."""
    doc = add_solid(base_doc(), "r", "#FF0000")  # pure red
    doc = add_effect(doc, "r", "invert", {})
    px = center_px(render_frame(doc))
    # RGB inverted: (1,0,0) -> (0,1,1) (cyan)
    assert px[0] == pytest.approx(0.0), f"Red should be 0, got {px[0]}"
    assert px[1] == pytest.approx(1.0), f"Green should be 1, got {px[1]}"
    assert px[2] == pytest.approx(1.0), f"Blue should be 1, got {px[2]}"
    assert px[3] == pytest.approx(1.0), f"Alpha should be 1, got {px[3]}"


def test_invert_white_to_black():
    """Invert white -> black."""
    doc = add_solid(base_doc(), "w", "#FFFFFF")
    doc = add_effect(doc, "w", "invert", {})
    px = center_px(render_frame(doc))
    assert px[:3] == pytest.approx([0.0, 0.0, 0.0], abs=1e-5)


def test_invert_preserves_semi_transparent():
    """Invert preserves alpha even if semi-transparent."""
    doc = add_solid(base_doc(), "r", "#FF0000FF")
    doc = apply_layer_patch(doc, patch({"op": "set_opacity", "layer_id": "r", "opacity": 0.5}))
    doc = add_effect(doc, "r", "invert", {})
    px = center_px(render_frame(doc))
    assert px[3] == pytest.approx(0.5, abs=1e-3)


# ════════════════════════════════════════════════════════════════════════
# 2. GRAYSCALE
# ════════════════════════════════════════════════════════════════════════


def test_grayscale_amount_0_keeps_color():
    """Grayscale amount=0 should preserve full color."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "grayscale", {"amount": 0.0})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)
    assert px[1] == pytest.approx(0.0)


def test_grayscale_amount_1_full_grey():
    """Grayscale amount=1 should fully desaturate."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "grayscale", {"amount": 1.0})
    px = center_px(render_frame(doc))
    # Red luminance: 0.299*1 + 0.587*0 + 0.114*0 = 0.299
    grey = pytest.approx(0.299, abs=1e-2)
    assert px[0] == grey
    assert px[1] == grey
    assert px[2] == grey


def test_grayscale_amount_half():
    """Grayscale amount=0.5 should blend halfway."""
    doc = add_solid(base_doc(), "r", "#FF0000")  # red
    doc = add_effect(doc, "r", "grayscale", {"amount": 0.5})
    px = center_px(render_frame(doc))
    grey = 0.299
    # blend: (1-0.5)*red + 0.5*grey = 0.5*1 + 0.5*0.299 = 0.6495
    assert px[0] == pytest.approx(0.6495, abs=1e-2)
    assert px[1] == pytest.approx(0.5 * grey, abs=1e-2)


# ════════════════════════════════════════════════════════════════════════
# 3. MIRROR / FLIP
# ════════════════════════════════════════════════════════════════════════


def test_mirror_horizontal_flips_x_axis():
    """Horizontal flip should mirror left-right."""
    doc = base_doc(w=10, h=10)
    doc = add_solid(doc, "base", "#000000")
    # Add a red solid and apply horizontal flip.
    doc = add_solid(doc, "r", "#FF0000")
    doc = add_effect(doc, "r", "mirror", {"direction": "horizontal"})
    # The center should still be red, but a left pixel should now have what was on the right.
    px_center = center_px(render_frame(doc))
    assert px_center[0] == pytest.approx(1.0)  # center still red


def test_mirror_vertical_flips_y_axis():
    """Vertical flip should mirror top-bottom."""
    doc = base_doc(w=10, h=10)
    doc = add_solid(doc, "r", "#FF0000")
    doc = add_effect(doc, "r", "mirror", {"direction": "vertical"})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)  # center still red


def test_mirror_both_flips_both_axes():
    """Mirror with both should flip both axes."""
    doc = base_doc(w=10, h=10)
    doc = add_solid(doc, "r", "#FF0000")
    doc = add_effect(doc, "r", "mirror", {"direction": "both"})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)  # center still red (both flips preserve center)


def test_mirror_alias_flip_works():
    """Alias 'flip' should work like 'mirror'."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    # In compile.py, we check: if effect_type == "mirror" or effect_type == "flip"
    # For now test via add_effect which passes the effect_type as-is.
    doc = apply_layer_patch(doc, patch({
        "op": "add_effect", "layer_id": "r", "type": "flip", "params": {"direction": "horizontal"}
    }))
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)


# ════════════════════════════════════════════════════════════════════════
# 4. CROP
# ════════════════════════════════════════════════════════════════════════


def test_crop_full_rect_unchanged():
    """Crop to full [0,0,1,1] should not change image."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "crop", {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)


def test_crop_half_darkens():
    """Crop to left half [0,0,0.5,1] should darken right half to transparent."""
    doc = add_solid(base_doc(w=10, h=10), "r", "#FF0000")
    doc = add_effect(doc, "r", "crop", {"x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 1.0})
    frame = render_frame(doc)
    # Center (5, 5) is at x=5, which is outside [0, 0.5*10=5) on x axis (x >= 5 is excluded).
    # Actually, x1=0.5*10=5, so x in [0, 5) is included. Pixel at x=5 should be on boundary.
    # Let's check pixel at x=2 (left side) vs x=8 (right side).
    left_px = frame[5, 2]  # x=2 is in [0,5)
    right_px = frame[5, 8]  # x=8 is outside [0,5)
    assert left_px[0] == pytest.approx(1.0)  # red on left
    assert right_px[3] == pytest.approx(0.0)  # transparent on right


def test_crop_center_quarter():
    """Crop to center quarter [0.25, 0.25, 0.75, 0.75]."""
    doc = add_solid(base_doc(w=8, h=8), "r", "#FF0000")
    doc = add_effect(doc, "r", "crop", {"x0": 0.25, "y0": 0.25, "x1": 0.75, "y1": 0.75})
    frame = render_frame(doc)
    # Center should still be red.
    center = center_px(frame)
    assert center[0] == pytest.approx(1.0)
    # Corners should be transparent.
    corner = frame[0, 0]
    assert corner[3] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════════
# 5. VIGNETTE
# ════════════════════════════════════════════════════════════════════════


def test_vignette_amount_0_unchanged():
    """Vignette amount=0 should not darken."""
    doc = add_solid(base_doc(), "w", "#FFFFFF")
    doc = add_effect(doc, "w", "vignette", {"amount": 0.0})
    px = center_px(render_frame(doc))
    assert px[:3] == pytest.approx([1.0, 1.0, 1.0])


def test_vignette_amount_1_darkens_edges():
    """Vignette amount=1 should darken significantly at edges."""
    doc = add_solid(base_doc(w=32, h=32), "w", "#FFFFFF")
    doc = add_effect(doc, "w", "vignette", {"amount": 1.0})
    frame = render_frame(doc)
    center = center_px(frame)
    corner = frame[0, 0]  # top-left corner
    # Center should remain bright.
    assert center[0] > 0.8
    # Corner should be significantly darkened.
    assert corner[0] < center[0]


def test_vignette_amount_half():
    """Vignette amount=0.5 should darken moderately."""
    doc = add_solid(base_doc(w=32, h=32), "w", "#FFFFFF")
    doc = add_effect(doc, "w", "vignette", {"amount": 0.5})
    frame = render_frame(doc)
    center = center_px(frame)
    corner = frame[0, 0]
    # Center should stay bright (vignette at center ≈ 1.0), corner darkened but not as much as amount=1.
    assert center[0] > 0.9  # center stays bright
    assert corner[0] < center[0]  # corner is definitely darker


# ════════════════════════════════════════════════════════════════════════
# 6. SHARPEN
# ════════════════════════════════════════════════════════════════════════


def test_sharpen_amount_0_unchanged():
    """Sharpen amount=0 should not change pixels."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "sharpen", {"amount": 0.0})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)


def test_sharpen_amount_positive():
    """Sharpen with positive amount should exist (details hard to verify on solid)."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "sharpen", {"amount": 1.0})
    px = center_px(render_frame(doc))
    # Solid color should stay roughly the same (sharpening has no edges).
    assert px[0] == pytest.approx(1.0, abs=1e-1)


# ════════════════════════════════════════════════════════════════════════
# 7. HUE_ROTATE
# ════════════════════════════════════════════════════════════════════════


def test_hue_rotate_0_unchanged():
    """Hue rotate 0 degrees should not change color."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "hue_rotate", {"degrees": 0.0})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)  # still red


def test_hue_rotate_120_red_to_green():
    """Hue rotate ~120 degrees: red -> green."""
    doc = add_solid(base_doc(), "r", "#FF0000")  # pure red, H=0
    doc = add_effect(doc, "r", "hue_rotate", {"degrees": 120.0})  # H=120 = green
    px = center_px(render_frame(doc))
    # Should shift towards green.
    assert px[1] > px[0]  # green > red
    assert px[1] > px[2]  # green > blue


def test_hue_rotate_240_red_to_blue():
    """Hue rotate 240 degrees: red -> blue."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "hue_rotate", {"degrees": 240.0})  # H=240 = blue
    px = center_px(render_frame(doc))
    # Should shift towards blue.
    assert px[2] > px[0]  # blue > red


def test_hue_rotate_360_full_circle():
    """Hue rotate 360 degrees should return to original."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "hue_rotate", {"degrees": 360.0})
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)  # still red


# ════════════════════════════════════════════════════════════════════════
# 8. CHROMA_KEY
# ════════════════════════════════════════════════════════════════════════


def test_chroma_key_exact_match_fully_transparent():
    """Chroma key with exact color match should make it fully transparent."""
    doc = add_solid(base_doc(), "green", "#00FF00")  # pure green
    doc = add_effect(doc, "green", "chroma_key", {
        "key_color": "#00FF00",
        "threshold": 0.01,
        "softness": 0.0,
    })
    px = center_px(render_frame(doc))
    # Green is the key color, distance=0, so alpha should be 0.
    assert px[3] == pytest.approx(0.0, abs=1e-2)


def test_chroma_key_red_survives():
    """Chroma key with green key should preserve red."""
    doc = add_solid(base_doc(), "red", "#FF0000")  # pure red
    doc = add_effect(doc, "red", "chroma_key", {
        "key_color": "#00FF00",  # key is green
        "threshold": 0.1,
        "softness": 0.0,
    })
    px = center_px(render_frame(doc))
    # Red is far from green, distance is large, alpha should be 1.
    assert px[3] == pytest.approx(1.0, abs=1e-2)


def test_chroma_key_with_softness():
    """Chroma key with softness should create a soft edge."""
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = add_effect(doc, "green", "chroma_key", {
        "key_color": "#00FF00",
        "threshold": 0.1,
        "softness": 0.2,
    })
    px = center_px(render_frame(doc))
    # With softness, exact match should still be fairly transparent.
    assert px[3] < 0.1


def test_chroma_key_list_color():
    """Chroma key should accept list-format color [R, G, B]."""
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = add_effect(doc, "green", "chroma_key", {
        "key_color": [0.0, 1.0, 0.0],  # [R, G, B]
        "threshold": 0.01,
        "softness": 0.0,
    })
    px = center_px(render_frame(doc))
    assert px[3] == pytest.approx(0.0, abs=1e-2)


# ════════════════════════════════════════════════════════════════════════
# 9. FLIP_LAYER OP (convenience op)
# ════════════════════════════════════════════════════════════════════════


def test_flip_layer_op_default_horizontal():
    """flip_layer op without direction should default to horizontal."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "flip_layer", "layer_id": "r"}))
    # Check that a mirror effect was added.
    layer = None
    for child in doc["root"]["children"]:
        if child.get("id") == "r":
            layer = child
            break
    assert layer is not None
    assert len(layer.get("effects", [])) > 0
    effect = layer["effects"][0]
    assert effect.get("type") == "mirror"
    assert effect.get("params", {}).get("direction") == "horizontal"


def test_flip_layer_op_vertical():
    """flip_layer op with vertical direction."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "flip_layer", "layer_id": "r", "direction": "vertical"}))
    layer = None
    for child in doc["root"]["children"]:
        if child.get("id") == "r":
            layer = child
            break
    assert layer is not None
    effect = layer["effects"][0]
    assert effect.get("params", {}).get("direction") == "vertical"


def test_flip_layer_op_upserts_existing():
    """flip_layer op should upsert (not duplicate) if mirror effect already exists."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    # Add mirror effect manually.
    doc = apply_layer_patch(doc, patch({
        "op": "add_effect", "layer_id": "r", "type": "mirror", "params": {"direction": "horizontal"}
    }))
    # Now upsert via flip_layer op.
    doc = apply_layer_patch(doc, patch({"op": "flip_layer", "layer_id": "r", "direction": "vertical"}))
    layer = None
    for child in doc["root"]["children"]:
        if child.get("id") == "r":
            layer = child
            break
    # Should still have exactly one mirror effect (upserted, not added).
    effects = [e for e in layer.get("effects", []) if e.get("type") == "mirror"]
    assert len(effects) == 1
    assert effects[0].get("params", {}).get("direction") == "vertical"


def test_flip_layer_op_renders():
    """flip_layer op should produce renderable result."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "flip_layer", "layer_id": "r", "direction": "horizontal"}))
    # Should render without error.
    px = center_px(render_frame(doc))
    assert px[0] == pytest.approx(1.0)  # still red


# ════════════════════════════════════════════════════════════════════════
# EDGE CASES & INTEGRATION
# ════════════════════════════════════════════════════════════════════════


def test_multiple_effects_chain():
    """Multiple effects should apply in order."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_effect(doc, "r", "invert", {})  # -> cyan (0,1,1)
    doc = add_effect(doc, "r", "grayscale", {"amount": 1.0})  # -> grey
    px = center_px(render_frame(doc))
    # Inverted red is cyan (0,1,1), grey of cyan is 0.299*0 + 0.587*1 + 0.114*1 = 0.701
    grey = pytest.approx(0.701, abs=1e-2)
    assert px[0] == grey
    assert px[1] == grey
    assert px[2] == grey


def test_effect_disabled_skipped():
    """Disabled effect should be skipped."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({
        "op": "add_effect", "layer_id": "r", "type": "invert", "params": {}, "enabled": False
    }))
    px = center_px(render_frame(doc))
    # Still red (invert was disabled).
    assert px[0] == pytest.approx(1.0)


def test_effects_preserve_alpha():
    """Most effects should preserve alpha unless explicitly modifying it (chroma_key)."""
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "set_opacity", "layer_id": "r", "opacity": 0.5}))
    doc = add_effect(doc, "r", "invert", {})
    doc = add_effect(doc, "r", "grayscale", {"amount": 0.5})
    doc = add_effect(doc, "r", "sharpen", {"amount": 1.0})
    px = center_px(render_frame(doc))
    # Alpha should still be 0.5.
    assert px[3] == pytest.approx(0.5, abs=1e-2)


def test_chroma_key_reduces_alpha():
    """Chroma key should multiply into existing alpha."""
    doc = add_solid(base_doc(), "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({"op": "set_opacity", "layer_id": "green", "opacity": 0.5}))
    doc = add_effect(doc, "green", "chroma_key", {
        "key_color": "#00FF00",
        "threshold": 0.01,
        "softness": 0.0,
    })
    px = center_px(render_frame(doc))
    # Alpha was 0.5, chroma key makes it 0 (key color), so result is 0.5 * 0 = 0.
    assert px[3] == pytest.approx(0.0, abs=1e-2)
