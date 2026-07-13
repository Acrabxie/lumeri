"""The scene-template component library — coverage, expansion, render & drift.

These tests pin the *library* the same way ``test_lumenframe_catalog`` pins the
op vocabulary: every registered template has a catalogue entry, every documented
param really exists on its function (no doc drift), and every template expands to
valid ops that dispatch and render to a canvas-sized frame without raising. A
palette round-trip and the ``theme`` helpers are checked too, plus a regression
for the keyframe-easing fix the library depends on.
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from lumenframe import model
from lumenframe.ops import apply_layer_patch
from lumenframe.preview import preview_frames
from lumenframe.templates import (
    SHARED_PARAMS,
    TEMPLATE_CATALOG,
    TEMPLATES,
    describe_templates,
    expand_template,
    template_catalog,
    template_names,
    theme,
)


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _empty_doc():
    return {"root": model.new_layer("composition"), "selection": []}


def _param_base(token: str) -> str:
    """Strip a catalogue param token (``items* (list[str])``) to its bare name."""
    return token.split("(")[0].strip().rstrip("*").strip()


# ── coverage / drift guards ────────────────────────────────────────────────


class TestCatalogCoverage:
    def test_every_template_has_a_catalog_entry_and_vice_versa(self):
        registered = set(TEMPLATES)
        documented = {e["name"] for e in TEMPLATE_CATALOG}
        assert registered == documented, (
            f"TEMPLATES and TEMPLATE_CATALOG drifted: "
            f"only registered={registered - documented}, only documented={documented - registered}"
        )

    def test_catalog_is_a_fresh_copy(self):
        a = template_catalog()
        a[0]["name"] = "mutated"
        assert template_catalog()[0]["name"] != "mutated"

    def test_documented_params_exist_on_the_function(self):
        """Every param a catalogue entry names must be a real function param.

        This is the anti-drift guard: a template can't advertise a param it
        doesn't accept (the shared styling params live in SHARED_PARAMS).
        """
        for entry in TEMPLATE_CATALOG:
            fn = TEMPLATES[entry["name"]]
            sig = set(inspect.signature(fn).parameters)
            for token in entry.get("params", []):
                name = _param_base(token)
                assert name in sig or name in SHARED_PARAMS, (
                    f"{entry['name']}: documented param {name!r} is not on the function"
                )

    def test_styled_templates_accept_every_shared_param(self):
        """The new styled templates all take the full SHARED_PARAMS set."""
        legacy = {"intro", "lower_third"}
        for name, fn in TEMPLATES.items():
            if name in legacy:
                continue
            sig = set(inspect.signature(fn).parameters)
            missing = set(SHARED_PARAMS) - sig
            assert not missing, f"{name} is missing shared params {missing}"


# ── expansion / dispatch / render ──────────────────────────────────────────


class TestExpansionAndRender:
    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_expands_to_valid_ops(self, name):
        ops = expand_template(name, {})
        assert ops and all(isinstance(o, dict) and "op" in o for o in ops)

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_applies_and_renders(self, name):
        """Each template dispatches through apply_template and previews a frame."""
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_template", "template": name, "params": {}}
        ))
        assert out["root"]["children"], f"{name} produced no layers"
        frames = preview_frames(out, [0, 1], strict=False)
        assert len(frames) == 2
        for _idx, rgba in frames:
            assert rgba.shape[2] == 4  # canvas-sized RGBA

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_layer_ids_are_unique(self, name):
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_template", "template": name, "params": {}}
        ))
        ids = [c["id"] for c in out["root"]["children"]]
        assert len(ids) == len(set(ids)), f"{name} produced duplicate ids: {ids}"

    def test_prefix_avoids_id_clash_across_two_applies(self):
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_template", "template": "stat_card", "params": {"prefix": "a"}},
            {"op": "apply_template", "template": "stat_card", "params": {"prefix": "b"}},
        ))
        ids = [c["id"] for c in out["root"]["children"]]
        assert len(ids) == len(set(ids)) and len(ids) >= 4

    def test_content_params_reach_the_layers(self):
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_template", "template": "stat_card",
             "params": {"value": "42%", "label": "conversion"}}
        ))
        texts = [c["props"].get("text") for c in out["root"]["children"] if c["type"] == "text"]
        assert "42%" in texts and "conversion" in texts


# ── palette / theme ────────────────────────────────────────────────────────


class TestPaletteAndTheme:
    def test_palette_name_restyles_output(self):
        """Passing a palette name changes the colours the ops carry."""
        def accent_of(pal):
            ops = expand_template("title_card", {"title": "X", "palette": pal})
            rule = next(o for o in ops if o.get("op") == "add_shape")
            return rule["fill"]

        assert accent_of("ink") == theme.PALETTES["ink"]["accent"]
        assert accent_of("sunset") == theme.PALETTES["sunset"]["accent"]
        assert accent_of("ink") != accent_of("sunset")

    def test_partial_palette_override_merges_over_default(self):
        p = theme.resolve_palette({"accent": "#ff0055"})
        assert p["accent"] == "#ff0055"
        # every other role inherited from the default palette
        assert p["bg"] == theme.PALETTES[theme.DEFAULT_PALETTE]["bg"]

    def test_unknown_palette_falls_back_not_raises(self):
        assert theme.resolve_palette("does-not-exist") == theme.PALETTES[theme.DEFAULT_PALETTE]

    def test_type_scale_tracks_height(self):
        assert theme.type_size("title", 2160) > theme.type_size("title", 1080)
        assert theme.type_size("title", 1080) > theme.type_size("body", 1080)
        # unknown role never returns 0
        assert theme.type_size("nonsense", 1080) >= 10

    def test_norm_bridge_round_trips_centre(self):
        assert theme.nx(0, 1920) == pytest.approx(0.5)
        assert theme.ny(0, 1080) == pytest.approx(0.5)


# ── agent-facing description ────────────────────────────────────────────────


class TestDescribeTemplates:
    def test_lists_every_template_and_palette(self):
        text = describe_templates()
        for name in template_names():
            assert name in text, f"{name} missing from describe_templates()"
        for pal in theme.palette_names():
            assert pal in text
        for shared in SHARED_PARAMS:
            assert shared in text

    def test_injected_into_describe_ops(self):
        from lumenframe import describe_ops

        text = describe_ops()
        assert "Scene template library" in text
        assert "bullet_list" in text and "stat_card" in text


# ── regression: keyframe easing actually renders ────────────────────────────


class TestEasingRegression:
    def test_easing_for_maps_to_track_vocabulary(self):
        """`_easing_for` must emit names KeyframeTrack.add_keyframe accepts."""
        from lumenframe.compile import _easing_for
        from gemia.video.keyframe import KeyframeTrack

        track = KeyframeTrack()
        for interp in ("linear", "hold", "ease", "ease_in", "ease_out", "ease_in_out", "bezier"):
            # add_keyframe raises on an unknown easing — so this pins the mapping.
            track.add_keyframe(0.0, 1.0, _easing_for(interp))

    def test_ease_out_keyframe_renders(self):
        """A non-linear set_keyframe used to crash compile; now it renders."""
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "add_layer", "type": "text", "id": "t", "text": "Hi",
             "color": "#ffffff", "font_size": 80, "at_time": 0.0, "duration": 2.0},
            {"op": "set_keyframe", "layer_id": "t", "property": "opacity", "t": 0.0, "value": 0.0, "interp": "linear"},
            {"op": "set_keyframe", "layer_id": "t", "property": "opacity", "t": 0.5, "value": 1.0, "interp": "ease_out"},
        ))
        frames = preview_frames(out, [0, 20], strict=False)
        assert len(frames) == 2
