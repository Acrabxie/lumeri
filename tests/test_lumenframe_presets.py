"""CapCut-style preset ops: speed_ramp, animate_text, apply_template.

These ops are sugar over the existing primitives (set_time_remap / set_keyframe /
op dispatch). The tests pin the *concrete* output: the time-remap slope of a
``hero`` ramp, the scale keyframes a ``pop`` writes, and the layer list a
``lower_third`` template produces (plus that it renders without error).
"""
from __future__ import annotations

import pytest

from lumenframe import model
from lumenframe.ops import apply_layer_patch, LayerPatchError
from lumenframe.text_anim import text_anim_ops, TextAnimError, POP_OVERSHOOT
from lumenframe.templates import TEMPLATES, expand_template, template_names


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _video_doc(lid="clip1", *, start=0.0, duration=4.0, source_in=0.0, source_out=4.0):
    """A doc with a single video layer carrying a real source range."""
    return {
        "root": model.new_layer("composition", children=[
            model.new_layer("video", id=lid, start=start, duration=duration,
                            source_in=source_in, source_out=source_out),
        ]),
        "selection": [],
    }


def _text_doc(lid="title", *, start=0.0, duration=3.0):
    return {
        "root": model.new_layer("composition", children=[
            model.new_layer("text", id=lid, start=start, duration=duration,
                            props={"text": "Hello"}),
        ]),
        "selection": [],
    }


# ── (A) speed_ramp ──────────────────────────────────────────────────────


def _slope_between(keyframes, t_lo, t_hi):
    """Local slope d(source)/d(output) of an eval'd time_remap over [t_lo,t_hi]."""
    remap = {"keyframes": keyframes, "extrapolate": "hold"}
    s_lo = model.eval_time_remap(remap, t_lo)
    s_hi = model.eval_time_remap(remap, t_hi)
    return (s_hi - s_lo) / (t_hi - t_lo)


class TestSpeedRamp:
    def test_hero_has_slow_middle_slope_below_one(self):
        """'hero' produces a time_remap whose middle segment has slope < 1."""
        doc = _video_doc(duration=4.0, source_out=4.0)
        out = apply_layer_patch(doc, patch(
            {"op": "speed_ramp", "layer_id": "clip1", "preset": "hero"}
        ))
        layer = out["root"]["children"][0]
        remap = layer["time_remap"]
        kfs = remap["keyframes"]
        assert kfs, "speed_ramp must emit time_remap keyframes"
        # Output duration is preserved (endpoints span the full 4s output).
        assert kfs[0]["t"] == pytest.approx(0.0)
        assert kfs[-1]["t"] == pytest.approx(4.0)
        # Middle of the clip (around output t=2s) must run slower than realtime.
        mid_slope = _slope_between(kfs, 1.5, 2.5)
        assert mid_slope < 1.0, f"hero middle slope must be < 1 (slow-mo), got {mid_slope}"
        # And the edges must be faster than realtime to make up the time.
        edge_slope = _slope_between(kfs, 0.0, 0.5)
        assert edge_slope > 1.0, f"hero edge slope must be > 1 (fast), got {edge_slope}"

    def test_speed_ramp_clears_constant_speed(self):
        """A remap supersedes constant speed; speed is reset to 1.0."""
        doc = _video_doc()
        doc["root"]["children"][0]["speed"] = 2.0
        out = apply_layer_patch(doc, patch(
            {"op": "speed_ramp", "layer_id": "clip1", "preset": "montage"}
        ))
        assert out["root"]["children"][0]["speed"] == 1.0

    def test_montage_middle_is_fast(self):
        """'montage' fast-forwards through the middle (slope > 1)."""
        doc = _video_doc(duration=4.0, source_out=4.0)
        out = apply_layer_patch(doc, patch(
            {"op": "speed_ramp", "layer_id": "clip1", "preset": "montage"}
        ))
        kfs = out["root"]["children"][0]["time_remap"]["keyframes"]
        mid_slope = _slope_between(kfs, 1.5, 2.5)
        assert mid_slope > 1.0, f"montage middle slope must be > 1 (fast), got {mid_slope}"

    @pytest.mark.parametrize("preset", ["montage", "hero", "bullet", "ease_in", "ease_out"])
    def test_all_presets_emit_a_monotonic_curve(self, preset):
        """Every preset yields a valid, non-decreasing output->source mapping."""
        doc = _video_doc(duration=4.0, source_out=4.0)
        out = apply_layer_patch(doc, patch(
            {"op": "speed_ramp", "layer_id": "clip1", "preset": preset}
        ))
        kfs = out["root"]["children"][0]["time_remap"]["keyframes"]
        ts = [k["t"] for k in kfs]
        vals = [k["value"] for k in kfs]
        assert ts == sorted(ts)
        assert vals == sorted(vals), f"{preset}: source time must never run backwards"
        # Endpoints anchor source to its full range (0 -> source_out).
        assert vals[0] == pytest.approx(0.0)
        assert vals[-1] == pytest.approx(4.0)

    def test_unknown_preset_is_e_arg(self):
        doc = _video_doc()
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "speed_ramp", "layer_id": "clip1", "preset": "nope"}
            ))
        assert exc.value.code == "E_ARG"

    def test_missing_layer_is_not_found(self):
        doc = _video_doc()
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "speed_ramp", "layer_id": "ghost", "preset": "hero"}
            ))
        assert exc.value.code == "E_NOT_FOUND"


# ── (B) animate_text ────────────────────────────────────────────────────


class TestAnimateText:
    def test_pop_writes_scale_keyframes_0_to_overshoot_to_1(self):
        """'pop' writes scale keyframes 0 -> ~1.2 -> 1 at start/mid/end frames."""
        doc = _text_doc(start=0.0, duration=3.0)
        out = apply_layer_patch(doc, patch(
            {"op": "animate_text", "layer_id": "title", "preset": "pop", "duration": 0.6}
        ))
        layer = out["root"]["children"][0]
        kfs = layer["keyframes"]
        for prop in ("transform.scale_x", "transform.scale_y"):
            track = kfs[prop]
            assert len(track) == 3, f"{prop} should have start/overshoot/settle keyframes"
            # start, mid (0.3s for a 0.6s animation starting at 0), end (0.6s)
            assert track[0]["t"] == pytest.approx(0.0)
            assert track[1]["t"] == pytest.approx(0.3)
            assert track[2]["t"] == pytest.approx(0.6)
            assert track[0]["value"] == pytest.approx(0.0)
            assert track[1]["value"] == pytest.approx(POP_OVERSHOOT)  # ~1.2
            assert track[1]["value"] > 1.0  # genuine overshoot
            assert track[2]["value"] == pytest.approx(1.0)
        # Opacity also fades in.
        assert kfs["opacity"][0]["value"] == pytest.approx(0.0)
        assert kfs["opacity"][-1]["value"] == pytest.approx(1.0)

    def test_pop_respects_layer_start_offset(self):
        """Keyframes are absolute: a layer starting at 2.0 anchors there."""
        doc = _text_doc(start=2.0, duration=3.0)
        out = apply_layer_patch(doc, patch(
            {"op": "animate_text", "layer_id": "title", "preset": "pop", "duration": 0.6}
        ))
        track = out["root"]["children"][0]["keyframes"]["transform.scale_x"]
        assert track[0]["t"] == pytest.approx(2.0)
        assert track[-1]["t"] == pytest.approx(2.6)

    @pytest.mark.parametrize("preset", ["fade_in_words", "pop", "wave", "rise"])
    def test_presets_only_touch_opacity_and_transform(self, preset):
        """No preset introduces a non-standard property (resolver-safe)."""
        doc = _text_doc()
        out = apply_layer_patch(doc, patch(
            {"op": "animate_text", "layer_id": "title", "preset": preset, "duration": 0.5}
        ))
        kfs = out["root"]["children"][0]["keyframes"]
        allowed = {"opacity", "transform.x", "transform.y",
                   "transform.scale_x", "transform.scale_y", "transform.rotation"}
        assert set(kfs) <= allowed, f"{preset} wrote unexpected props: {set(kfs) - allowed}"
        assert kfs, f"{preset} must write at least one keyframe track"

    def test_rise_fades_and_translates_up(self):
        doc = _text_doc()
        out = apply_layer_patch(doc, patch(
            {"op": "animate_text", "layer_id": "title", "preset": "rise", "duration": 0.5}
        ))
        kfs = out["root"]["children"][0]["keyframes"]
        y = kfs["transform.y"]
        assert y[0]["value"] > y[-1]["value"]  # travels upward (toward 0)
        assert y[-1]["value"] == pytest.approx(0.0)
        assert kfs["opacity"][0]["value"] == pytest.approx(0.0)
        assert kfs["opacity"][-1]["value"] == pytest.approx(1.0)

    def test_unknown_preset_is_e_arg(self):
        doc = _text_doc()
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "animate_text", "layer_id": "title", "preset": "nope"}
            ))
        assert exc.value.code == "E_ARG"

    def test_text_anim_ops_helper_rejects_bad_preset_and_duration(self):
        with pytest.raises(TextAnimError):
            text_anim_ops("x", "nope", layer_start=0.0, layer_duration=1.0)
        with pytest.raises(TextAnimError):
            text_anim_ops("x", "pop", layer_start=0.0, layer_duration=1.0, duration=0.0)


# ── (C) apply_template ──────────────────────────────────────────────────


class TestApplyTemplate:
    def test_lower_third_yields_text_and_shape_layers(self):
        """'lower_third' yields a doc with a text layer and a shape layer."""
        doc = {"root": model.new_layer("composition"), "selection": []}
        out = apply_layer_patch(doc, patch(
            {"op": "apply_template", "template": "lower_third",
             "params": {"text": "Jane Doe", "subtitle": "Director"}}
        ))
        children = out["root"]["children"]
        types = sorted(c["type"] for c in children)
        assert types == ["shape", "text"], f"expected text+shape, got {types}"
        text_layer = next(c for c in children if c["type"] == "text")
        shape_layer = next(c for c in children if c["type"] == "shape")
        # Caption carries both lines; shape carries a colour fill.
        assert "Jane Doe" in text_layer["props"]["text"]
        assert "Director" in text_layer["props"]["text"]
        assert shape_layer["props"].get("color")
        # The text layer got its rise-in keyframes (template ran animate_text).
        assert text_layer["keyframes"], "lower_third should animate its caption"

    def test_lower_third_renders_without_error(self):
        """The expanded doc compiles + previews without raising."""
        from lumenframe.preview import preview_frames

        doc = {"root": model.new_layer("composition"), "selection": []}
        out = apply_layer_patch(doc, patch(
            {"op": "apply_template", "template": "lower_third",
             "params": {"text": "Live", "duration": 2.0}}
        ))
        # Non-strict compile: a shape layer has no resolver content and is simply
        # skipped, but the text layer renders — and nothing raises.
        frames = preview_frames(out, [0, 1], strict=False)
        assert len(frames) == 2
        for _idx, rgba in frames:
            assert rgba.shape[2] == 4  # canvas-sized RGBA

    def test_intro_yields_solid_and_text_and_renders(self):
        from lumenframe.preview import preview_frames

        doc = {"root": model.new_layer("composition"), "selection": []}
        out = apply_layer_patch(doc, patch(
            {"op": "apply_template", "template": "intro",
             "params": {"title": "My Film", "duration": 2.0}}
        ))
        types = sorted(c["type"] for c in out["root"]["children"])
        assert types == ["solid", "text"]
        frames = preview_frames(out, [0], strict=False)
        assert frames[0][1].shape[2] == 4

    def test_template_can_be_applied_twice_without_id_clash(self):
        """Distinct prefixes keep two applications of the same template separate."""
        doc = {"root": model.new_layer("composition"), "selection": []}
        out = apply_layer_patch(doc, patch(
            {"op": "apply_template", "template": "lower_third",
             "params": {"text": "A", "prefix": "lt1"}},
            {"op": "apply_template", "template": "lower_third",
             "params": {"text": "B", "prefix": "lt2"}},
        ))
        ids = [c["id"] for c in out["root"]["children"]]
        assert len(ids) == len(set(ids)) == 4

    def test_unknown_template_is_e_arg(self):
        doc = {"root": model.new_layer("composition"), "selection": []}
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "apply_template", "template": "nope"}
            ))
        assert exc.value.code == "E_ARG"

    def test_bad_params_is_e_arg(self):
        doc = {"root": model.new_layer("composition"), "selection": []}
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, patch(
                {"op": "apply_template", "template": "lower_third",
                 "params": {"not_a_real_param": 1}}
            ))
        assert exc.value.code == "E_ARG"

    def test_registry_lists_the_legacy_templates(self):
        # The library has grown (see test_lumenframe_component_library); the two
        # original templates must still be registered and expand to pure ops.
        assert {"lower_third", "intro"} <= set(template_names())
        assert "lower_third" in TEMPLATES and "intro" in TEMPLATES
        # The pure expansion is op dicts only.
        ops = expand_template("intro", {"title": "X"})
        assert all(isinstance(o, dict) and "op" in o for o in ops)
