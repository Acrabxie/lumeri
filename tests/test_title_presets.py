"""Tests for gemia.text.title_presets — named parameterized title animations."""
from __future__ import annotations

import pytest

from gemia.text.title_presets import (
    Keyframe,
    TitlePreset,
    get_preset,
    list_presets,
    preset_catalog,
    register_preset,
)


class TestBuiltinPresets:
    EXPECTED = {"fade_in", "slide_up", "scale_pop", "typewriter", "quiet_hold", "accent_wipe"}

    def test_all_builtins_registered(self):
        available = set(list_presets())
        assert self.EXPECTED <= available, f"missing: {self.EXPECTED - available}"

    def test_get_preset_returns_frozen_dataclass(self):
        for name in self.EXPECTED:
            preset = get_preset(name)
            assert isinstance(preset, TitlePreset)
            assert preset.name == name
            assert len(preset.enter) >= 2

    def test_unknown_preset_raises_key_error(self):
        with pytest.raises(KeyError, match="unknown title preset"):
            get_preset("nonexistent_animation_42")

    def test_catalog_returns_stable_dicts(self):
        catalog = preset_catalog()
        assert len(catalog) >= len(self.EXPECTED)
        for entry in catalog:
            assert "name" in entry
            assert "enter" in entry
            assert isinstance(entry["enter"], list)
            for kf in entry["enter"]:
                assert "progress" in kf
                assert "opacity" in kf


class TestKeyframeContract:
    def test_keyframe_round_trip(self):
        kf = Keyframe(progress=0.5, opacity=0.8, scale=1.1, y_offset_ratio=-0.2, easing="ease-in-out")
        d = kf.to_dict()
        assert d["progress"] == 0.5
        assert d["opacity"] == 0.8
        assert d["easing"] == "ease-in-out"

    def test_preset_enter_exit_are_tuples(self):
        for name in list_presets():
            preset = get_preset(name)
            assert isinstance(preset.enter, tuple)
            assert isinstance(preset.hold, tuple)
            assert isinstance(preset.exit, tuple)

    def test_enter_starts_at_zero_ends_at_one(self):
        for name in list_presets():
            preset = get_preset(name)
            assert preset.enter[0].progress == 0.0
            assert preset.enter[-1].progress == 1.0

    def test_exit_starts_at_zero_ends_at_one_when_present(self):
        for name in list_presets():
            preset = get_preset(name)
            if preset.exit:
                assert preset.exit[0].progress == 0.0
                assert preset.exit[-1].progress == 1.0


class TestCustomPreset:
    def test_register_and_retrieve(self):
        custom = TitlePreset(
            name="_test_custom_bounce",
            description="test-only bounce preset",
            enter=(
                Keyframe(progress=0.0, opacity=0.0, scale=0.5),
                Keyframe(progress=0.5, opacity=1.0, scale=1.15, easing="ease-out"),
                Keyframe(progress=1.0, opacity=1.0, scale=1.0),
            ),
            default_duration_ms=400,
        )
        register_preset(custom)
        retrieved = get_preset("_test_custom_bounce")
        assert retrieved is custom
        assert "_test_custom_bounce" in list_presets()

    def test_to_dict_carries_params(self):
        preset = get_preset("typewriter")
        d = preset.to_dict()
        assert d["params"]["per_char"] is True
        assert d["params"]["char_stagger_ms"] == 45


class TestPresetSemantics:
    def test_quiet_hold_has_no_transition(self):
        preset = get_preset("quiet_hold")
        assert preset.default_duration_ms == 0
        assert all(kf.opacity == 1.0 for kf in preset.enter)

    def test_scale_pop_has_overshoot(self):
        preset = get_preset("scale_pop")
        scales = [kf.scale for kf in preset.enter]
        assert max(scales) > 1.0, "scale_pop should overshoot past 1.0"

    def test_slide_up_has_vertical_motion(self):
        preset = get_preset("slide_up")
        offsets = [kf.y_offset_ratio for kf in preset.enter]
        assert offsets[0] > 0, "should start below"
        assert offsets[-1] == 0.0, "should land at anchor"
