"""Depth-of-field / background-blur for the ``pip`` op + the ``focus_pull`` op.

These are additive depth-of-field sugar over the existing ``gaussian_blur``
effect: nothing new touches the compile path — both ops only append a normal
``gaussian_blur`` effect (same shape ``add_effect`` produces) to the right
siblings, so the inset/foreground pops in focus over a softened background.

What is pinned here:

* ``pip(..., blur_background=R)`` appends a ``gaussian_blur`` of radius ``R`` to
  **every sibling below** the pip (lower z = behind it) and to *nothing else* —
  not the pip layer, not layers above it, not the optional border/shadow helper;
* ``focus_pull(layer_id, blur=R)`` appends the blur to **all siblings except**
  the focused layer (foreground stays sharp);
* with ``blur_background`` absent / ``0`` the produced doc is **byte-identical**
  to the plain pip (no effect added anywhere);
* a render smoke: a doc with a textured background + a blurred-bg PiP compiles
  and renders, and the background region is *measurably softer* (lower local
  variance) than the same doc rendered without the background blur.
"""
from __future__ import annotations

import json

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


def _blur_effects(layer):
    """All gaussian_blur effects on a layer (normalised dicts)."""
    return [e for e in (layer.get("effects") or []) if e.get("type") == "gaussian_blur"]


def _by_id(out, lid):
    return next(c for c in out["root"]["children"] if c.get("id") == lid)


# ── pip blur_background ────────────────────────────────────────────────────


class TestPipBlurBackground:
    def test_blurs_only_siblings_below_the_pip(self):
        # z-order (children order) = bg0, bg1, PIP, over0.  Below the pip: bg0,bg1.
        doc = _doc(_vid("bg0"), _vid("bg1"), _vid("pip"), _vid("over0"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "scale": 0.3, "blur_background": 8}
        ))
        # Below the pip -> exactly one gaussian_blur of radius 8.
        for lid in ("bg0", "bg1"):
            fx = _blur_effects(_by_id(out, lid))
            assert len(fx) == 1, lid
            assert fx[0]["params"]["radius"] == pytest.approx(8.0), lid
            assert fx[0]["type"] == "gaussian_blur"
        # The pip itself and the layer above it stay sharp.
        assert _blur_effects(_by_id(out, "pip")) == []
        assert _blur_effects(_by_id(out, "over0")) == []

    def test_radius_value_is_honoured(self):
        doc = _doc(_vid("bg"), _vid("pip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "blur_background": 15}
        ))
        fx = _blur_effects(_by_id(out, "bg"))
        assert len(fx) == 1 and fx[0]["params"]["radius"] == pytest.approx(15.0)

    def test_pip_at_bottom_blurs_nothing(self):
        # pip is the first child -> no siblings below -> no blur added anywhere.
        doc = _doc(_vid("pip"), _vid("over0"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "blur_background": 8}
        ))
        assert _blur_effects(_by_id(out, "pip")) == []
        assert _blur_effects(_by_id(out, "over0")) == []

    def test_does_not_blur_the_border_helper(self):
        # The border/shadow helper frames the pip; it must stay sharp even though
        # it is spliced in *below* the pip.
        doc = _doc(_vid("bg"), _vid("pip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "radius": 16,
             "border": {"color": "#ffffff", "width": 3}, "blur_background": 8}
        ))
        children = out["root"]["children"]
        # order: bg, helper(shape), pip
        types = [c["type"] for c in children]
        assert types == ["video", "shape", "video"]
        helper = children[1]
        assert _blur_effects(helper) == []          # the frame stays sharp
        assert _blur_effects(children[0]) and children[0]["id"] == "bg"  # bg blurred
        assert _blur_effects(children[2]) == []     # the pip stays sharp

    def test_negative_blur_background_raises(self):
        doc = _doc(_vid("bg"), _vid("pip"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "pip", "layer_id": "pip", "corner": "br", "blur_background": -3}
            ))
        assert exc.value.code == "E_RANGE"

    def test_blur_appends_rather_than_replacing_existing_effects(self):
        bg = _vid("bg")
        bg["effects"] = [model._normalize_effect(
            {"type": "color_grade", "id": "cg1", "params": {"contrast": 1.2}}
        )]
        doc = _doc(bg, _vid("pip"))
        out = apply_layer_patch(doc, patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "blur_background": 6}
        ))
        effects = _by_id(out, "bg")["effects"]
        types = [e["type"] for e in effects]
        assert types == ["color_grade", "gaussian_blur"]


# ── byte-identical when blur is absent / 0 ─────────────────────────────────


class TestPipUnchangedWithoutBlur:
    def _baseline_and_variant(self, blur_kw):
        # One source doc, deep-copied, so the only difference between the two
        # results is the pip op's kwargs (the random doc id stays identical).
        import copy

        src = _doc(_vid("bg0"), _vid("bg1"), _vid("pip"))
        baseline = apply_layer_patch(copy.deepcopy(src), patch(
            {"op": "pip", "layer_id": "pip", "corner": "br", "scale": 0.3,
             "margin": 0.04, "radius": 24}
        ))
        op = {"op": "pip", "layer_id": "pip", "corner": "br", "scale": 0.3,
              "margin": 0.04, "radius": 24}
        op.update(blur_kw)
        variant = apply_layer_patch(copy.deepcopy(src), patch(op))
        return baseline, variant

    def test_absent_blur_background_is_byte_identical(self):
        baseline, variant = self._baseline_and_variant({})
        assert json.dumps(variant, sort_keys=True) == json.dumps(baseline, sort_keys=True)

    def test_zero_blur_background_is_byte_identical(self):
        baseline, variant = self._baseline_and_variant({"blur_background": 0})
        assert json.dumps(variant, sort_keys=True) == json.dumps(baseline, sort_keys=True)
        # And no gaussian_blur leaked onto any layer.
        for lid in ("bg0", "bg1", "pip"):
            assert _blur_effects(_by_id(variant, lid)) == []


# ── focus_pull ─────────────────────────────────────────────────────────────


class TestFocusPull:
    def test_blurs_all_siblings_except_the_focus(self):
        doc = _doc(_vid("a"), _vid("focus"), _vid("b"), _vid("c"))
        out = apply_layer_patch(doc, patch(
            {"op": "focus_pull", "layer_id": "focus", "blur": 10}
        ))
        # Siblings (both behind and in front) blurred; focus stays sharp.
        for lid in ("a", "b", "c"):
            fx = _blur_effects(_by_id(out, lid))
            assert len(fx) == 1 and fx[0]["params"]["radius"] == pytest.approx(10.0), lid
        assert _blur_effects(_by_id(out, "focus")) == []

    def test_missing_layer_id_raises(self):
        doc = _doc(_vid("a"), _vid("focus"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch({"op": "focus_pull", "blur": 10}))
        assert exc.value.code == "E_ARG"

    def test_unknown_layer_raises(self):
        doc = _doc(_vid("a"), _vid("focus"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch({"op": "focus_pull", "layer_id": "ghost", "blur": 10}))
        assert exc.value.code == "E_NOT_FOUND"

    def test_nonpositive_blur_raises(self):
        doc = _doc(_vid("a"), _vid("focus"))
        with pytest.raises(LayerPatchError) as e0:
            apply_layer_patch(doc, patch({"op": "focus_pull", "layer_id": "focus", "blur": 0}))
        assert e0.value.code == "E_RANGE"
        with pytest.raises(LayerPatchError) as eneg:
            apply_layer_patch(doc, patch({"op": "focus_pull", "layer_id": "focus", "blur": -2}))
        assert eneg.value.code == "E_RANGE"

    def test_focus_pull_only_layer_blurs_nothing(self):
        doc = _doc(_vid("focus"))
        out = apply_layer_patch(doc, patch({"op": "focus_pull", "layer_id": "focus", "blur": 8}))
        assert _blur_effects(_by_id(out, "focus")) == []


# ── catalog wiring ─────────────────────────────────────────────────────────


class TestCatalogWiring:
    def test_focus_pull_is_a_registered_core_op(self):
        assert registry.op_source("focus_pull") == "core"

    def test_focus_pull_and_pip_have_catalog_entries(self):
        from lumenframe.catalog import CORE_OPS_CATALOG

        entries = {e["op"]: e for e in CORE_OPS_CATALOG}
        assert "focus_pull" in entries
        # pip's entry now documents blur_background.
        pip = entries["pip"]
        assert any("blur_background" in a for a in pip["args"])


# ── render smoke + variance proof ──────────────────────────────────────────


def _write_noise_png(path, w=320, h=180, seed=7):
    """A high-frequency RGB noise texture: high local variance, so a gaussian
    blur measurably softens it."""
    from PIL import Image

    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, mode="RGB").save(path)


def _noise_doc(blur_background, tmp_path, *, width=320, height=180):
    """A textured background image + a small solid PiP in the bottom-right."""
    png = tmp_path / "noise.png"
    _write_noise_png(str(png), w=width, h=height)

    doc = model.empty_doc(width=width, height=height)
    doc["assets"] = [{"id": "bgimg", "kind": "image", "path": str(png)}]
    bg = model.new_layer("image", id="bg", start=0.0, duration=2.0)
    bg["asset_id"] = "bgimg"
    fg = model.new_layer("solid", id="pip", start=0.0, duration=2.0)
    fg["props"]["color"] = "#ff0000"
    doc["root"]["children"] = [bg, fg]

    op = {"op": "pip", "layer_id": "pip", "corner": "br", "scale": 0.3, "margin": 0.04}
    if blur_background:
        op["blur_background"] = blur_background
    return apply_layer_patch(doc, patch(op))


def _local_variance(frame_rgb, *, win=8):
    """Mean of per-window variance over a grid — a proxy for local sharpness."""
    gray = frame_rgb.mean(axis=2)
    h, w = gray.shape
    vs = []
    for y in range(0, h - win, win):
        for x in range(0, w - win, win):
            vs.append(float(gray[y:y + win, x:x + win].var()))
    return float(np.mean(vs))


class TestRenderSmokeVariance:
    def test_blurred_background_renders_and_is_softer(self, tmp_path):
        from lumenframe.compile import compile_to_layer_stack

        W, H = 320, 180
        sharp = _noise_doc(0, tmp_path, width=W, height=H)
        registry.reset_for_tests()
        blurred = _noise_doc(12, tmp_path, width=W, height=H)

        # Sanity: the blurred-bg doc actually carried a gaussian_blur on the bg.
        bg_fx = _blur_effects(_by_id(blurred, "bg"))
        assert len(bg_fx) == 1 and bg_fx[0]["params"]["radius"] == pytest.approx(12.0)

        sharp_frame = compile_to_layer_stack(sharp, strict=False).render_frame(0)
        blurred_frame = compile_to_layer_stack(blurred, strict=False).render_frame(0)

        assert sharp_frame.shape == (H, W, 4)
        assert blurred_frame.shape == (H, W, 4)

        # Measure a background-only region (top-left half) — the PiP is bottom-right.
        region = (slice(0, H // 2), slice(0, W // 2))
        v_sharp = _local_variance(sharp_frame[region][..., :3])
        v_blur = _local_variance(blurred_frame[region][..., :3])

        # The blurred background must be measurably softer (lower local variance).
        assert v_blur < v_sharp * 0.6, (v_blur, v_sharp)
        # Stash for the proof line.
        TestRenderSmokeVariance.v_sharp = v_sharp
        TestRenderSmokeVariance.v_blur = v_blur
