"""M1 compile bridge — render real pixels from a lumenframe document."""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import _centred_position, compile_to_layer_stack


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def center_px(frame):
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


# ── basics ───────────────────────────────────────────────────────────────


def test_single_solid_fills_canvas():
    doc = add_solid(base_doc(), "r", "#FF0000")
    stack = compile_to_layer_stack(doc)
    assert (stack.width, stack.height, stack.total_frames) == (64, 48, 10)
    px = center_px(stack.render_frame(0))
    assert px[0] == pytest.approx(1.0) and px[1] == pytest.approx(0.0) and px[3] == pytest.approx(1.0)


def test_top_layer_covers_bottom():
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = add_solid(doc, "g", "#00FF00")  # added later -> on top
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[1] == pytest.approx(1.0) and px[0] == pytest.approx(0.0)


def test_opacity_lowers_alpha_not_colour():
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "set_opacity", "layer_id": "r", "opacity": 0.5}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[0] == pytest.approx(1.0) and px[3] == pytest.approx(0.5, abs=1e-3)


def test_invisible_layer_is_skipped():
    doc = add_solid(base_doc(), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "set_visibility", "layer_id": "r", "visible": False}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[3] == pytest.approx(0.0)  # nothing drawn


def test_translate_off_canvas_clears_centre():
    doc = add_solid(base_doc(64, 48), "r", "#FF0000")
    doc = apply_layer_patch(doc, patch({"op": "set_transform", "layer_id": "r", "x": 64}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[3] == pytest.approx(0.0)  # shifted fully right -> centre empty


def test_time_window_controls_visibility_over_frames():
    doc = add_solid(base_doc(fps=10), "r", "#FF0000", start=0.5, duration=0.5)  # frames 5..10
    stack = compile_to_layer_stack(doc)
    assert center_px(stack.render_frame(0))[3] == pytest.approx(0.0)   # before start
    assert center_px(stack.render_frame(6))[3] == pytest.approx(1.0)   # inside window


# ── nesting ───────────────────────────────────────────────────────────────


def test_composition_nesting_renders_child():
    doc = base_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_layer", "id": "comp", "type": "composition", "duration": 1.0}))
    doc = add_solid(doc, "inner", "#0000FF", duration=1.0)
    doc = apply_layer_patch(doc, patch({"op": "move_layer", "layer_id": "inner", "parent_id": "comp"}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[2] == pytest.approx(1.0) and px[0] == pytest.approx(0.0)  # blue from nested comp


# ── track mattes ────────────────────────────────────────────────────────────


def test_luma_matte_black_source_hides_layer():
    doc = base_doc()
    doc = add_solid(doc, "black", "#000000", visible=False)  # matte source, hidden
    doc = add_solid(doc, "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "green",
                                        "mask": {"kind": "luma_matte", "source_layer_id": "black"}}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[3] == pytest.approx(0.0)  # luma 0 -> fully matted out


def test_luma_matte_white_source_keeps_layer():
    doc = base_doc()
    doc = add_solid(doc, "white", "#FFFFFF", visible=False)
    doc = add_solid(doc, "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "green",
                                        "mask": {"kind": "luma_matte", "source_layer_id": "white"}}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[1] == pytest.approx(1.0) and px[3] == pytest.approx(1.0)


def test_matte_source_is_not_rendered_itself():
    # White matte source must not paint the canvas white; only the green shows.
    doc = base_doc()
    doc = add_solid(doc, "white", "#FFFFFF")  # visible True, but used as matte -> auto-skipped
    doc = add_solid(doc, "green", "#00FF00")
    doc = apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "green",
                                        "mask": {"kind": "alpha_matte", "source_layer_id": "white"}}))
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    assert px[1] == pytest.approx(1.0) and px[0] == pytest.approx(0.0)


# ── keyframes ────────────────────────────────────────────────────────────────


def test_opacity_keyframes_animate_alpha():
    doc = add_solid(base_doc(fps=10), "r", "#FF0000", duration=1.0)
    doc = apply_layer_patch(doc, patch(
        {"op": "set_keyframe", "layer_id": "r", "property": "opacity", "t": 0.0, "value": 0.0},
        {"op": "set_keyframe", "layer_id": "r", "property": "opacity", "t": 1.0, "value": 1.0},
    ))
    stack = compile_to_layer_stack(doc)
    a0 = center_px(stack.render_frame(0))[3]
    a_mid = center_px(stack.render_frame(5))[3]
    a_end = center_px(stack.render_frame(9))[3]
    assert a0 == pytest.approx(0.0, abs=1e-2)
    assert 0.3 < a_mid < 0.7
    assert a_end > 0.85


# ── resolver hook ────────────────────────────────────────────────────────────


def test_resolver_supplies_media_content():
    doc = base_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_layer", "id": "vid", "type": "video", "duration": 1.0, "asset_id": "a1"}))
    doc["assets"] = [{"id": "a1", "media_kind": "video", "path": "/fake.mp4"}]

    def resolver(layer, ctx):
        if layer["type"] != "video":
            return None
        frame = np.zeros((ctx.height, ctx.width, 4), dtype=np.float32)
        frame[..., 1] = 1.0  # green
        frame[..., 3] = 1.0
        assert ctx.asset(layer["asset_id"])["path"] == "/fake.mp4"
        return lambda _i: frame.copy()

    px = center_px(compile_to_layer_stack(doc, resolver=resolver).render_frame(0))
    assert px[1] == pytest.approx(1.0)


def test_strict_mode_raises_on_unresolved_media():
    from lumenframe.compile import CompileError
    doc = base_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_layer", "id": "vid", "type": "video", "duration": 1.0}))
    with pytest.raises(CompileError):
        compile_to_layer_stack(doc, strict=True)


# ── transform math (unit) ─────────────────────────────────────────────────────


def test_centred_position_identity_and_rotation():
    assert _centred_position(100, 80, 1.0, 0.0, 0.0, 0.0) == (0, 0)
    assert _centred_position(100, 80, 1.0, 0.0, 10.0, -5.0) == (10, -5)
    # 90° rotation swaps the bounding box; centre stays put.
    assert _centred_position(100, 80, 1.0, 90.0, 0.0, 0.0) == (10, -10)
