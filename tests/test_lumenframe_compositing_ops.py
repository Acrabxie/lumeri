"""Multi-layer compositing sugar: set_blend / pip / crossfade / add_gradient /
add_shape.

These ops are convenience macros over the existing primitives (blend_mode /
transform+mask / opacity-keyframes / add_layer) and introduce no new compile
path. The tests pin the *concrete* output:

* ``set_blend`` stores the validated mode (and rejects a typo);
* ``pip`` writes the exact corner transform (scale + canvas-centre offset) and a
  full-frame rounded-rect mask — proven both as stored values and as carved
  pixels (transparent corners, opaque centre) through a real compile;
* ``crossfade`` writes the expected opacity keyframes on *both* layers;
* ``add_gradient`` / ``add_shape`` build layers matching the shared layer-schema
  contract; gradient/shape *renderers* are added in parallel (c2-layers), so the
  render-smoke asserts the layer dict/props are correct and that a populated doc
  compiles + previews without raising.

The geometry proofs (computed independently of the implementation):

  pip @ W=1920 H=1080 scale=0.3 margin=0.04 corner=br
    x_mag = W*(0.5 - margin - scale/2) = 1920*0.31 = 595.2
    y_mag = H*(0.5 - margin - scale/2) = 1080*0.31 = 334.8
    br => x=+595.2, y=+334.8
  pip radius 24px => mask radius frac = 24/min(W,H) = 24/1080 = 0.0222...
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import model, registry
from lumenframe.ops import apply_layer_patch, LayerPatchError


def setup_function(_fn):
    registry.reset_for_tests()


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _doc(*children, width=1920, height=1080):
    doc = model.empty_doc(width=width, height=height)
    doc["root"]["children"] = list(children)
    return doc


def _vid(lid, *, start=0.0, duration=5.0):
    return model.new_layer("video", id=lid, start=start, duration=duration,
                           source_in=0.0, source_out=duration)


def _solid(lid, color="#ff0000", *, start=0.0, duration=1.0):
    layer = model.new_layer("solid", id=lid, start=start, duration=duration)
    layer["props"]["color"] = color
    return layer


# ── set_blend ──────────────────────────────────────────────────────────────


class TestSetBlend:
    def test_stores_the_mode(self):
        doc = _doc(_vid("L"))
        out = apply_layer_patch(doc, patch({"op": "set_blend", "layer_id": "L", "mode": "screen"}))
        assert out["root"]["children"][0]["blend_mode"] == "screen"

    def test_every_supported_mode_round_trips(self):
        for mode in sorted(model.BLEND_MODES):
            registry.reset_for_tests()
            doc = _doc(_vid("L"))
            out = apply_layer_patch(doc, patch({"op": "set_blend", "layer_id": "L", "mode": mode}))
            assert out["root"]["children"][0]["blend_mode"] == mode

    def test_unknown_mode_is_rejected_up_front(self):
        doc = _doc(_vid("L"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch({"op": "set_blend", "layer_id": "L", "mode": "glow"}))
        assert exc.value.code == "E_ARG"

    def test_missing_args_raise(self):
        doc = _doc(_vid("L"))
        with pytest.raises(LayerPatchError) as e1:
            apply_layer_patch(doc, patch({"op": "set_blend", "layer_id": "L"}))
        assert e1.value.code == "E_ARG"
        with pytest.raises(LayerPatchError) as e2:
            apply_layer_patch(doc, patch({"op": "set_blend", "mode": "screen"}))
        assert e2.value.code == "E_ARG"

    def test_unknown_layer_raises(self):
        doc = _doc(_vid("L"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch({"op": "set_blend", "layer_id": "nope", "mode": "screen"}))
        assert exc.value.code == "E_NOT_FOUND"


# ── pip ──────────────────────────────────────────────────────────────────


class TestPip:
    def test_corner_transform_and_scale_br(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "clip", "corner": "br", "scale": 0.3, "margin": 0.04, "radius": 24}
        ))
        t = out["root"]["children"][0]["transform"]
        # scale set on both axes
        assert t["scale_x"] == pytest.approx(0.3)
        assert t["scale_y"] == pytest.approx(0.3)
        # x_mag = 1920*(0.5-0.04-0.15)=595.2 ; y_mag = 1080*0.31 = 334.8
        assert t["x"] == pytest.approx(595.2)
        assert t["y"] == pytest.approx(334.8)

    def test_all_four_corners_flip_signs(self):
        # |x|, |y| identical across corners; only the sign flips per corner.
        expected = {
            "br": (+595.2, +334.8),
            "bl": (-595.2, +334.8),
            "tr": (+595.2, -334.8),
            "tl": (-595.2, -334.8),
        }
        for corner, (ex, ey) in expected.items():
            registry.reset_for_tests()
            doc = _doc(_vid("clip"))
            out = apply_layer_patch(doc, patch(
                {"op": "pip", "layer_id": "clip", "corner": corner, "scale": 0.3, "margin": 0.04}
            ))
            t = out["root"]["children"][0]["transform"]
            assert t["x"] == pytest.approx(ex), corner
            assert t["y"] == pytest.approx(ey), corner

    def test_defaults_scale_and_margin(self):
        # scale default 0.3, margin default 0.04 -> same as the explicit case.
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "corner": "br"}))
        t = out["root"]["children"][0]["transform"]
        assert t["scale_x"] == pytest.approx(0.3)
        assert t["x"] == pytest.approx(595.2)
        assert t["y"] == pytest.approx(334.8)

    def test_explicit_xy_overrides_corner(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "clip", "x": -120, "y": 80, "scale": 0.25}
        ))
        t = out["root"]["children"][0]["transform"]
        assert t["x"] == pytest.approx(-120.0)
        assert t["y"] == pytest.approx(80.0)
        assert t["scale_x"] == pytest.approx(0.25)

    def test_rounded_rect_mask_is_attached(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "clip", "corner": "br", "radius": 24}
        ))
        mask = out["root"]["children"][0]["mask"]
        assert mask["kind"] == "shape"
        shape = mask["shape"]
        assert shape["type"] == "rectangle"
        assert shape["rect"] == [0.0, 0.0, 1.0, 1.0]
        # radius (px) stored as a fraction of the smaller canvas dimension.
        assert shape["radius"] == pytest.approx(24.0 / 1080.0)

    def test_zero_radius_gives_square_full_frame_mask(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "corner": "br"}))
        mask = out["root"]["children"][0]["mask"]
        assert mask["kind"] == "shape"
        assert mask["shape"]["radius"] == pytest.approx(0.0)
        assert mask["shape"]["rect"] == [0.0, 0.0, 1.0, 1.0]

    def test_border_emits_helper_shape_layer_beneath(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "clip", "corner": "br", "scale": 0.3, "radius": 16,
             "border": {"color": "#ffffff", "width": 3}}
        ))
        children = out["root"]["children"]
        # helper shape sits *before* (beneath) the pip so it composites behind it.
        assert [c["type"] for c in children] == ["shape", "video"]
        helper = children[0]
        assert helper["props"]["kind"] == "rect"
        assert helper["props"]["stroke"] == {"color": "#ffffff", "width": 3.0}
        assert helper["props"]["rect"] == [0.0, 0.0, 1.0, 1.0]
        assert helper["props"]["radius"] == pytest.approx(16.0)
        assert helper["props"]["opacity_baked"] is False
        # helper shares the pip's transform so it frames it exactly.
        pip = children[1]
        assert helper["transform"] == pip["transform"]

    def test_shadow_emits_helper_with_dark_fill(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "clip", "corner": "tl", "shadow": True}
        ))
        children = out["root"]["children"]
        assert [c["type"] for c in children] == ["shape", "video"]
        assert children[0]["props"]["shadow"] is True
        assert children[0]["props"]["fill"] == "#000000"

    def test_no_border_no_shadow_no_helper(self):
        doc = _doc(_vid("clip"))
        out = apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "corner": "br"}))
        assert [c["type"] for c in out["root"]["children"]] == ["video"]

    def test_bad_corner_and_scale_and_radius_raise(self):
        doc = _doc(_vid("clip"))
        with pytest.raises(LayerPatchError) as e_corner:
            apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "corner": "middle"}))
        assert e_corner.value.code == "E_ARG"
        with pytest.raises(LayerPatchError) as e_scale:
            apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "scale": 0}))
        assert e_scale.value.code == "E_RANGE"
        with pytest.raises(LayerPatchError) as e_radius:
            apply_layer_patch(doc, patch({"op": "pip", "layer_id": "clip", "radius": -5}))
        assert e_radius.value.code == "E_RANGE"

    def test_unknown_layer_raises(self):
        doc = _doc(_vid("clip"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch({"op": "pip", "layer_id": "ghost", "corner": "br"}))
        assert exc.value.code == "E_NOT_FOUND"

    def test_pip_mask_carves_rounded_corners_at_pixel_level(self):
        """Render-smoke + pixel proof: a solid (which renders) masked by a big
        rounded-rect has transparent corners and an opaque centre/edge-midpoint."""
        from lumenframe.compile import compile_to_layer_stack

        doc = _doc(_solid("S", "#ff0000"), width=200, height=200)
        # Fill the canvas (scale 1, x=y=0) with a large corner radius so the
        # rounded carve is unambiguous: radius 60px / 200 = 0.3 frac.
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "S", "x": 0, "y": 0, "scale": 1.0, "radius": 60}
        ))
        assert out["root"]["children"][0]["mask"]["shape"]["radius"] == pytest.approx(0.3)
        stack = compile_to_layer_stack(out, strict=False)
        assert len(stack.layers) == 1
        frame = stack.render_frame(0)
        assert frame.shape == (200, 200, 4)
        assert frame.dtype == np.float32
        # centre + edge-midpoints opaque; corners carved to fully transparent.
        assert float(frame[100, 100, 3]) == pytest.approx(1.0, abs=1e-3)
        assert float(frame[100, 2, 3]) == pytest.approx(1.0, abs=1e-3)
        assert float(frame[2, 100, 3]) == pytest.approx(1.0, abs=1e-3)
        assert float(frame[2, 2, 3]) == pytest.approx(0.0, abs=1e-3)
        assert float(frame[197, 197, 3]) == pytest.approx(0.0, abs=1e-3)


# ── crossfade ──────────────────────────────────────────────────────────────


class TestCrossfade:
    def test_writes_opacity_keyframes_on_both_layers(self):
        doc = _doc(_vid("A", start=0.0, duration=5.0), _vid("B", start=4.0, duration=5.0))
        out = apply_layer_patch(doc, patch(
            {"op": "crossfade", "from_id": "A", "to_id": "B", "duration": 1.0, "at": 4.0}
        ))
        a = next(c for c in out["root"]["children"] if c["id"] == "A")
        b = next(c for c in out["root"]["children"] if c["id"] == "B")
        # 'from' fades 1 -> 0 over [4.0, 5.0]
        assert a["keyframes"]["opacity"] == [
            {"t": 4.0, "value": 1.0, "interp": "linear"},
            {"t": 5.0, "value": 0.0, "interp": "linear"},
        ]
        # 'to' fades 0 -> 1 over the same window
        assert b["keyframes"]["opacity"] == [
            {"t": 4.0, "value": 0.0, "interp": "linear"},
            {"t": 5.0, "value": 1.0, "interp": "linear"},
        ]

    def test_at_defaults_to_from_layer_start(self):
        doc = _doc(_vid("A", start=2.0, duration=4.0), _vid("B", start=5.0, duration=4.0))
        out = apply_layer_patch(doc, patch(
            {"op": "crossfade", "from_id": "A", "to_id": "B", "duration": 0.5}
        ))
        a = next(c for c in out["root"]["children"] if c["id"] == "A")
        ts = [k["t"] for k in a["keyframes"]["opacity"]]
        assert ts == [2.0, 2.5]

    def test_default_duration_half_second(self):
        doc = _doc(_vid("A", start=0.0, duration=4.0), _vid("B", start=3.0, duration=4.0))
        out = apply_layer_patch(doc, patch(
            {"op": "crossfade", "from_id": "A", "to_id": "B", "at": 3.0}
        ))
        b = next(c for c in out["root"]["children"] if c["id"] == "B")
        assert [k["t"] for k in b["keyframes"]["opacity"]] == [3.0, 3.5]

    def test_rewrites_existing_keyframes_at_the_same_times(self):
        doc = _doc(_vid("A", start=0.0, duration=5.0), _vid("B", start=4.0, duration=5.0))
        first = apply_layer_patch(doc, patch(
            {"op": "crossfade", "from_id": "A", "to_id": "B", "duration": 1.0, "at": 4.0}
        ))
        again = apply_layer_patch(first, patch(
            {"op": "crossfade", "from_id": "A", "to_id": "B", "duration": 1.0, "at": 4.0}
        ))
        a = next(c for c in again["root"]["children"] if c["id"] == "A")
        # no duplicate keyframes at t=4.0 / t=5.0
        assert len(a["keyframes"]["opacity"]) == 2

    def test_bad_duration_and_missing_ids_raise(self):
        doc = _doc(_vid("A"), _vid("B"))
        with pytest.raises(LayerPatchError) as e_dur:
            apply_layer_patch(doc, patch(
                {"op": "crossfade", "from_id": "A", "to_id": "B", "duration": 0}
            ))
        assert e_dur.value.code == "E_RANGE"
        with pytest.raises(LayerPatchError) as e_arg:
            apply_layer_patch(doc, patch({"op": "crossfade", "from_id": "A"}))
        assert e_arg.value.code == "E_ARG"
        with pytest.raises(LayerPatchError) as e_nf:
            apply_layer_patch(doc, patch({"op": "crossfade", "from_id": "A", "to_id": "ghost"}))
        assert e_nf.value.code == "E_NOT_FOUND"


# ── add_gradient ─────────────────────────────────────────────────────────


class TestAddGradient:
    def test_linear_layer_dict_matches_contract(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_gradient", "id": "bg", "mode": "linear",
             "stops": [[0.0, "#000000"], [1.0, "#ffffff"]], "angle": 90}
        ))
        layer = out["root"]["children"][0]
        assert layer["type"] == "gradient"
        assert layer["id"] == "bg"
        props = layer["props"]
        assert props["mode"] == "linear"
        assert props["stops"] == [[0.0, "#000000"], [1.0, "#ffffff"]]
        assert props["angle"] == pytest.approx(90.0)

    def test_radial_layer_dict_matches_contract(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_gradient", "id": "bg", "mode": "radial",
             "stops": [[0.0, "#ffffff"], [1.0, "#000000"]],
             "center": [0.3, 0.7], "radius": 0.6}
        ))
        props = out["root"]["children"][0]["props"]
        assert props["mode"] == "radial"
        assert props["center"] == [0.3, 0.7]
        assert props["radius"] == pytest.approx(0.6)
        assert "angle" not in props

    def test_stops_are_sorted_and_clamped(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_gradient", "id": "bg", "mode": "linear",
             "stops": [[1.5, "#fff"], [-0.2, "#000"], [0.5, "#888"]]}
        ))
        props = out["root"]["children"][0]["props"]
        positions = [s[0] for s in props["stops"]]
        assert positions == sorted(positions)
        assert positions[0] >= 0.0 and positions[-1] <= 1.0
        assert positions == [0.0, 0.5, 1.0]

    def test_bad_mode_and_too_few_stops_raise(self):
        doc = _doc()
        with pytest.raises(LayerPatchError) as e_mode:
            apply_layer_patch(doc, patch(
                {"op": "add_gradient", "mode": "conic", "stops": [[0, "#000"], [1, "#fff"]]}
            ))
        assert e_mode.value.code == "E_ARG"
        with pytest.raises(LayerPatchError) as e_stops:
            apply_layer_patch(doc, patch(
                {"op": "add_gradient", "mode": "linear", "stops": [[0, "#000"]]}
            ))
        assert e_stops.value.code == "E_ARG"

    def test_render_smoke_doc_with_gradient_compiles(self):
        """The gradient renderer is added in parallel (c2-layers); here we assert
        the structure is correct and a populated doc compiles/previews w/o error.
        """
        from lumenframe.preview import preview_frames

        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_gradient", "id": "g", "mode": "linear",
             "stops": [[0.0, "#000000"], [1.0, "#ffffff"]], "angle": 45,
             "at_time": 0.0, "duration": 2.0}
        ))
        layer = out["root"]["children"][0]
        assert layer["type"] == "gradient" and layer["props"]["stops"]
        frames = preview_frames(out, [0], strict=False)
        assert len(frames) == 1
        idx, frame = frames[0]
        assert frame.shape == (1080, 1920, 4)


# ── add_shape ────────────────────────────────────────────────────────────


class TestAddShape:
    def test_rect_layer_dict_matches_contract(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "box", "kind": "rect", "fill": "#ff0044",
             "rect": [0.1, 0.1, 0.9, 0.9], "radius": 12,
             "stroke": {"color": "#ffffff", "width": 2}}
        ))
        layer = out["root"]["children"][0]
        assert layer["type"] == "shape"
        assert layer["id"] == "box"
        props = layer["props"]
        assert props["kind"] == "rect"
        assert props["fill"] == "#ff0044"
        assert props["rect"] == [0.1, 0.1, 0.9, 0.9]
        assert props["radius"] == pytest.approx(12.0)
        assert props["stroke"] == {"color": "#ffffff", "width": 2.0}
        assert props["opacity_baked"] is False

    def test_ellipse_with_centre_form(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "e", "kind": "ellipse", "fill": "#00ff00",
             "cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.2}
        ))
        props = out["root"]["children"][0]["props"]
        assert props["kind"] == "ellipse"
        assert props["cx"] == pytest.approx(0.5)
        assert props["cy"] == pytest.approx(0.5)
        assert props["rx"] == pytest.approx(0.3)
        assert props["ry"] == pytest.approx(0.2)

    def test_polygon_points(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "tri", "kind": "polygon", "fill": "#0000ff",
             "points": [[0.5, 0.1], [0.9, 0.9], [0.1, 0.9]]}
        ))
        props = out["root"]["children"][0]["props"]
        assert props["kind"] == "polygon"
        assert props["points"] == [[0.5, 0.1], [0.9, 0.9], [0.1, 0.9]]

    def test_null_fill_allowed(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "outline", "kind": "rect", "fill": None,
             "rect": [0.2, 0.2, 0.8, 0.8], "stroke": {"color": "#fff", "width": 4}}
        ))
        props = out["root"]["children"][0]["props"]
        assert props["fill"] is None
        assert props["stroke"]["width"] == pytest.approx(4.0)

    def test_default_rect_when_no_geometry(self):
        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "full", "kind": "rect", "fill": "#123456"}
        ))
        assert out["root"]["children"][0]["props"]["rect"] == [0.0, 0.0, 1.0, 1.0]

    def test_bad_kind_and_short_polygon_raise(self):
        doc = _doc()
        with pytest.raises(LayerPatchError) as e_kind:
            apply_layer_patch(doc, patch({"op": "add_shape", "kind": "star", "fill": "#fff"}))
        assert e_kind.value.code == "E_ARG"
        with pytest.raises(LayerPatchError) as e_poly:
            apply_layer_patch(doc, patch(
                {"op": "add_shape", "kind": "polygon", "points": [[0.1, 0.1]]}
            ))
        assert e_poly.value.code == "E_ARG"

    def test_render_smoke_doc_with_shape_compiles(self):
        """Shape renderer is added in parallel (c2-layers); assert structure and
        that a populated doc compiles/previews without raising."""
        from lumenframe.preview import preview_frames

        doc = _doc()
        out = apply_layer_patch(doc, patch(
            {"op": "add_shape", "id": "sh", "kind": "ellipse", "fill": "#00ff00",
             "rect": [0.2, 0.2, 0.8, 0.8], "at_time": 0.0, "duration": 2.0}
        ))
        layer = out["root"]["children"][0]
        assert layer["type"] == "shape" and layer["props"]["kind"] == "ellipse"
        frames = preview_frames(out, [0], strict=False)
        assert len(frames) == 1
        idx, frame = frames[0]
        assert frame.shape == (1080, 1920, 4)


# ── catalog wiring ─────────────────────────────────────────────────────────


class TestCatalogWiring:
    def test_new_ops_are_registered_core_ops(self):
        for name in ("set_blend", "pip", "crossfade", "add_gradient", "add_shape"):
            assert registry.op_source(name) == "core", name

    def test_new_ops_have_catalog_entries(self):
        from lumenframe.catalog import CORE_OPS_CATALOG

        catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
        for name in ("set_blend", "pip", "crossfade", "add_gradient", "add_shape"):
            assert name in catalog_ops, name
