"""Pixel-level tests for DaVinci-style colour tools in lumenframe.

Part (A) — curves: a 256-entry monotone LUT built from control points and
applied per channel (rgb / r / g / b / luma), alpha-preserving and stackable.

Part (B) — colour wheels: the lift / gamma / gain / temperature / tint params
wired into the ``color_grade`` render path (``_apply_color_grade``), with a
strict no-op guarantee for the identity settings (lift=0, gamma=1, gain=1).

All assertions are at the pixel level so they fail loudly on regressions.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe.compile import _apply_color_grade, _effect_color_grade
from lumenframe.effects.curves import apply_curves, build_curve_lut


# ── helpers ───────────────────────────────────────────────────────────────


def flat_rgba(r, g, b, a=1.0, h=4, w=4):
    """A flat HxWx4 float32 RGBA frame."""
    frame = np.zeros((h, w, 4), dtype=np.float32)
    frame[..., 0] = r
    frame[..., 1] = g
    frame[..., 2] = b
    frame[..., 3] = a
    return frame


def ramp_rgba(h=1, w=16):
    """A horizontal grey ramp 0..1 across width, RGBA float32 (alpha=1)."""
    xs = np.linspace(0.0, 1.0, w, dtype=np.float32)
    frame = np.zeros((h, w, 4), dtype=np.float32)
    for c in range(3):
        frame[..., c] = xs[np.newaxis, :]
    frame[..., 3] = 1.0
    return frame


IDENTITY = [[0.0, 0.0], [1.0, 1.0]]


# ── (A) curves: LUT construction ──────────────────────────────────────────


def test_identity_lut_is_ramp():
    """Identity curve [[0,0],[1,1]] -> LUT[i] == i/255."""
    lut = build_curve_lut(IDENTITY)
    assert lut.shape == (256,)
    expected = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    assert np.allclose(lut, expected, atol=1e-5)


def test_lut_is_monotone_nondecreasing_for_monotone_points():
    """An S-curve through monotone points yields a monotone-nondecreasing LUT."""
    s_curve = [[0.0, 0.0], [0.25, 0.12], [0.5, 0.5], [0.75, 0.88], [1.0, 1.0]]
    lut = build_curve_lut(s_curve)
    diffs = np.diff(lut)
    assert np.all(diffs >= -1e-6), "LUT must be monotone non-decreasing"


def test_lut_passes_through_control_points():
    """The LUT hits each control point (within sampling tolerance)."""
    pts = [[0.0, 0.1], [0.5, 0.4], [1.0, 0.9]]
    lut = build_curve_lut(pts)
    for x, y in pts:
        i = int(round(x * 255))
        assert lut[i] == pytest.approx(y, abs=2e-2)


# ── (A) curves: applying to frames ────────────────────────────────────────


def test_identity_curve_is_noop():
    """Identity curve [[0,0],[1,1]] is a pixel-exact no-op on RGB and alpha."""
    frame = ramp_rgba()
    for channel in ("rgb", "r", "g", "b", "luma"):
        out = apply_curves(frame, channel=channel, points=IDENTITY)
        assert np.allclose(out, frame, atol=1e-5), f"channel={channel} not a no-op"
        # alpha untouched.
        assert np.allclose(out[..., 3], frame[..., 3])


def test_scurve_increases_mid_contrast():
    """An S-curve pushes darks down and lights up -> mid contrast increases."""
    frame = ramp_rgba(w=64)
    s_curve = [[0.0, 0.0], [0.25, 0.12], [0.5, 0.5], [0.75, 0.88], [1.0, 1.0]]
    out = apply_curves(frame, channel="rgb", points=s_curve)
    # A shadow sample is darkened, a highlight sample is brightened.
    shadow_in = frame[0, 16, 0]
    shadow_out = out[0, 16, 0]
    highlight_in = frame[0, 48, 0]
    highlight_out = out[0, 48, 0]
    assert shadow_out < shadow_in - 1e-3, "S-curve must darken shadows"
    assert highlight_out > highlight_in + 1e-3, "S-curve must brighten highlights"
    # Local slope around mid-grey (contrast) exceeds the identity slope of 1.
    lo = out[0, 28, 0]
    hi = out[0, 36, 0]
    slope = (hi - lo) / (frame[0, 36, 0] - frame[0, 28, 0])
    assert slope > 1.0, "mid-tone contrast (slope) must increase"


def test_single_channel_curve_only_touches_that_channel():
    """A red-only curve changes R but leaves G/B/alpha untouched."""
    frame = flat_rgba(0.5, 0.5, 0.5, 0.7)
    lift_red = [[0.0, 0.2], [1.0, 1.0]]  # raises the red floor
    out = apply_curves(frame, channel="r", points=lift_red)
    assert out[0, 0, 0] > 0.5 + 1e-3, "R channel must change"
    assert out[0, 0, 1] == pytest.approx(0.5, abs=1e-5), "G unchanged"
    assert out[0, 0, 2] == pytest.approx(0.5, abs=1e-5), "B unchanged"
    assert out[0, 0, 3] == pytest.approx(0.7, abs=1e-5), "alpha preserved"


def test_luma_curve_preserves_hue_ratio_and_brightens():
    """A luma curve scales RGB together, preserving channel ratios."""
    frame = flat_rgba(0.4, 0.2, 0.1, 1.0)
    brighten = [[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]]  # lift mids
    out = apply_curves(frame, channel="luma", points=brighten)
    r, g, b = out[0, 0, 0], out[0, 0, 1], out[0, 0, 2]
    # Brighter overall.
    assert r + g + b > 0.4 + 0.2 + 0.1 + 1e-3
    # Hue (channel ratios) preserved: r:g:b stays 4:2:1.
    assert g == pytest.approx(r * 0.5, rel=2e-2)
    assert b == pytest.approx(r * 0.25, rel=3e-2)


def test_curves_are_stackable_and_alpha_preserving():
    """Applying two curves in sequence works and leaves alpha intact."""
    frame = ramp_rgba(w=32)
    s_curve = [[0.0, 0.0], [0.25, 0.12], [0.75, 0.88], [1.0, 1.0]]
    once = apply_curves(frame, channel="rgb", points=s_curve)
    twice = apply_curves(once, channel="rgb", points=s_curve)
    # Stacking compounds the contrast further at a shadow sample.
    assert twice[0, 8, 0] <= once[0, 8, 0] + 1e-6
    assert np.allclose(twice[..., 3], 1.0)
    # Original frame is not mutated (pure function).
    assert np.allclose(frame[..., 0][0], np.linspace(0.0, 1.0, 32), atol=1e-6)


# ── (B) colour wheels: identity no-op (golden) ────────────────────────────


def test_color_grade_legacy_path_unchanged_golden():
    """No wheel params -> identical to brightness/contrast/saturation only.

    Golden guard: passing lift=0/gamma=1/gain=1/temp=0/tint=0 explicitly must
    equal the legacy 3-arg call byte-for-byte.
    """
    rng = np.random.default_rng(7)
    frame = rng.random((8, 8, 4), dtype=np.float32)
    legacy = _apply_color_grade(frame, 0.1, 1.2, 0.9)
    explicit = _apply_color_grade(
        frame, 0.1, 1.2, 0.9,
        lift=0.0, gamma=1.0, gain=1.0, temperature=0.0, tint=0.0,
    )
    assert np.array_equal(legacy, explicit), "wheel identity must be golden-identical"


def test_lift0_gamma1_gain1_is_strict_noop():
    """lift=0, gamma=1, gain=1 leaves a plain frame exactly unchanged."""
    rng = np.random.default_rng(11)
    frame = rng.random((6, 6, 4), dtype=np.float32)
    out = _apply_color_grade(
        frame, 0.0, 1.0, 1.0,
        lift=0.0, gamma=1.0, gain=1.0,
    )
    assert np.array_equal(out, frame), "identity wheels must be a strict no-op"


def test_effect_color_grade_defaults_noop():
    """_effect_color_grade with empty params is a strict no-op (golden)."""
    rng = np.random.default_rng(13)
    frame = rng.random((5, 5, 4), dtype=np.float32)
    out = _effect_color_grade(frame, {}, ctx=None)
    assert np.array_equal(out, frame)


# ── (B) colour wheels: gain / lift / gamma behaviour ──────────────────────


def test_gain_2_doubles_highlights():
    """gain=2 doubles a mid value before clamping (0.4 -> 0.8)."""
    frame = flat_rgba(0.4, 0.4, 0.4, 1.0)
    out = _apply_color_grade(frame, 0.0, 1.0, 1.0, gain=2.0)
    assert out[0, 0, 0] == pytest.approx(0.8, abs=1e-4)
    assert out[0, 0, 1] == pytest.approx(0.8, abs=1e-4)
    assert out[0, 0, 2] == pytest.approx(0.8, abs=1e-4)
    # Highlights clamp at 1.0.
    bright = flat_rgba(0.7, 0.7, 0.7, 1.0)
    out2 = _apply_color_grade(bright, 0.0, 1.0, 1.0, gain=2.0)
    assert out2[0, 0, 0] == pytest.approx(1.0, abs=1e-5)
    # alpha preserved.
    assert out[0, 0, 3] == pytest.approx(1.0, abs=1e-5)


def test_lift_raises_black_floor():
    """lift>0 raises the black floor: a true-black pixel becomes lift."""
    black = flat_rgba(0.0, 0.0, 0.0, 1.0)
    out = _apply_color_grade(black, 0.0, 1.0, 1.0, lift=0.2)
    assert out[0, 0, 0] == pytest.approx(0.2, abs=1e-4)
    assert out[0, 0, 1] == pytest.approx(0.2, abs=1e-4)
    assert out[0, 0, 2] == pytest.approx(0.2, abs=1e-4)
    # A mid value is also offset upward.
    mid = flat_rgba(0.5, 0.5, 0.5, 1.0)
    out_mid = _apply_color_grade(mid, 0.0, 1.0, 1.0, lift=0.2)
    assert out_mid[0, 0, 0] == pytest.approx(0.7, abs=1e-4)
    assert out[0, 0, 3] == pytest.approx(1.0, abs=1e-5)


def test_gamma_gt1_brightens_mids_only():
    """gamma>1 brightens mid-tones but pins pure black and pure white."""
    black = flat_rgba(0.0, 0.0, 0.0, 1.0)
    mid = flat_rgba(0.5, 0.5, 0.5, 1.0)
    white = flat_rgba(1.0, 1.0, 1.0, 1.0)

    out_black = _apply_color_grade(black, 0.0, 1.0, 1.0, gamma=2.0)
    out_mid = _apply_color_grade(mid, 0.0, 1.0, 1.0, gamma=2.0)
    out_white = _apply_color_grade(white, 0.0, 1.0, 1.0, gamma=2.0)

    # Black stays black, white stays white (endpoints pinned).
    assert out_black[0, 0, 0] == pytest.approx(0.0, abs=1e-5)
    assert out_white[0, 0, 0] == pytest.approx(1.0, abs=1e-5)
    # Mids brighten: 0.5 ** (1/2) == sqrt(0.5) ~= 0.7071.
    assert out_mid[0, 0, 0] == pytest.approx(np.sqrt(0.5), abs=1e-4)
    assert out_mid[0, 0, 0] > 0.5 + 1e-3


def test_gamma_lt1_darkens_mids():
    """gamma<1 darkens mid-tones (complement of gamma>1)."""
    mid = flat_rgba(0.5, 0.5, 0.5, 1.0)
    out = _apply_color_grade(mid, 0.0, 1.0, 1.0, gamma=0.5)
    # 0.5 ** (1/0.5) == 0.25.
    assert out[0, 0, 0] == pytest.approx(0.25, abs=1e-4)


# ── (B) colour wheels: temperature / tint balance ─────────────────────────


def test_temperature_warm_shifts_red_up_blue_down():
    """Positive temperature warms: red rises, blue falls; identity at 0."""
    grey = flat_rgba(0.5, 0.5, 0.5, 1.0)
    warm = _apply_color_grade(grey, 0.0, 1.0, 1.0, temperature=0.5)
    assert warm[0, 0, 0] > 0.5 + 1e-3, "warm must raise red"
    assert warm[0, 0, 2] < 0.5 - 1e-3, "warm must lower blue"
    # temperature=0 is a no-op.
    noop = _apply_color_grade(grey, 0.0, 1.0, 1.0, temperature=0.0)
    assert np.array_equal(noop, grey)


def test_tint_green_magenta_balance():
    """Positive tint pushes toward green; identity at 0."""
    grey = flat_rgba(0.5, 0.5, 0.5, 1.0)
    green = _apply_color_grade(grey, 0.0, 1.0, 1.0, tint=0.5)
    assert green[0, 0, 1] > 0.5 + 1e-3, "+tint must raise green"
    assert green[0, 0, 0] < 0.5, "+tint must trim red toward magenta complement"
    noop = _apply_color_grade(grey, 0.0, 1.0, 1.0, tint=0.0)
    assert np.array_equal(noop, grey)
