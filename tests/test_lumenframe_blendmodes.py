"""Pixel-level tests for the extended blend-mode set in the compositing core.

Part (A) of the compositing-core hardening: ``gemia.video.layers._blend_colors``
gains the W3C separable blend modes plus the photographic ``add`` / ``subtract``
family, and ``lumenframe.compile._safe_blend`` whitelists them.

Every new mode is verified two ways on 64x64 solids:

* **Direct** — ``_blend_colors`` of an opaque source over an opaque backdrop.
  With both alphas == 1 the premultiplied composite collapses to the separable
  blend function ``f(cb, cs)``, so the centre pixel must equal the formula
  computed independently in this test (a real cross-check, not a tautology),
  and also the exact hand-computed constants documented inline.
* **End-to-end** — the same blend through ``LayerStack.render_frame`` to prove
  the compositor path actually selects the new mode.

The original four modes (normal / multiply / screen / overlay) are re-checked to
guard against regressions, and ``_safe_blend`` is checked for both whitelist
admission and safe degrade-to-normal on unknown modes.
"""
from __future__ import annotations

import numpy as np
import pytest

from gemia.video.layers import BLEND_MODES, Layer, LayerStack, _blend_colors
from lumenframe.compile import _safe_blend


SIZE = 64
CENTRE = SIZE // 2

# Backdrop (cb) and source (cs) colours chosen so each mode produces a distinct,
# non-degenerate centre pixel (channels exercise <0.5 and >0.5, and the dodge /
# burn edge cases at the channel extremes).
CB = (0.2, 0.6, 0.9)
CS = (0.4, 0.5, 0.3)


def _solid(color3, alpha=1.0):
    rgba = np.empty((SIZE, SIZE, 4), dtype=np.float32)
    rgba[..., 0] = color3[0]
    rgba[..., 1] = color3[1]
    rgba[..., 2] = color3[2]
    rgba[..., 3] = alpha
    return rgba


# ── reference (independent) implementations of each blend function ────────────


def _ref_blend(mode: str, cb: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """Per-channel reference for ``f(cb, cs)`` — computed here, not imported."""
    cb = np.asarray(cb, dtype=np.float64)
    cs = np.asarray(cs, dtype=np.float64)
    if mode == "normal":
        return cs
    if mode == "multiply":
        return cb * cs
    if mode == "screen":
        return 1.0 - (1.0 - cb) * (1.0 - cs)
    if mode == "overlay":
        return np.where(cb <= 0.5, 2.0 * cb * cs, 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs))
    if mode in ("add", "linear_dodge"):
        return np.clip(cb + cs, 0.0, 1.0)
    if mode == "lighten":
        return np.maximum(cb, cs)
    if mode == "darken":
        return np.minimum(cb, cs)
    if mode == "difference":
        return np.abs(cb - cs)
    if mode == "exclusion":
        return cb + cs - 2.0 * cb * cs
    if mode == "subtract":
        return np.clip(cb - cs, 0.0, 1.0)
    if mode == "hard_light":
        return np.where(cs <= 0.5, 2.0 * cb * cs, 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs))
    if mode == "soft_light":
        d = np.where(cb <= 0.25, ((16.0 * cb - 12.0) * cb + 4.0) * cb, np.sqrt(cb))
        return np.where(
            cs <= 0.5,
            cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
            cb + (2.0 * cs - 1.0) * (d - cb),
        )
    if mode == "color_dodge":
        out = np.empty_like(cb)
        for i in range(cb.shape[0]):
            b, s = cb[i], cs[i]
            if b <= 0.0:
                out[i] = 0.0
            elif s >= 1.0:
                out[i] = 1.0
            else:
                out[i] = min(1.0, b / (1.0 - s))
        return out
    if mode == "color_burn":
        out = np.empty_like(cb)
        for i in range(cb.shape[0]):
            b, s = cb[i], cs[i]
            if b >= 1.0:
                out[i] = 1.0
            elif s <= 0.0:
                out[i] = 0.0
            else:
                out[i] = max(0.0, min(1.0, 1.0 - (1.0 - b) / s))
        return out
    raise AssertionError(f"unhandled mode {mode}")


NEW_MODES = (
    "add",
    "linear_dodge",
    "lighten",
    "darken",
    "difference",
    "exclusion",
    "subtract",
    "hard_light",
    "soft_light",
    "color_dodge",
    "color_burn",
)

# Hand-computed centre pixels for CB over CS (see _ref_blend / task formulas).
EXPECTED_CENTRE = {
    "add": [0.6, 1.0, 1.0],
    "linear_dodge": [0.6, 1.0, 1.0],
    "lighten": [0.4, 0.6, 0.9],
    "darken": [0.2, 0.5, 0.3],
    "difference": [0.2, 0.1, 0.6],
    "exclusion": [0.44, 0.5, 0.66],
    "subtract": [0.0, 0.1, 0.6],
    "hard_light": [0.16, 0.6, 0.54],
    "soft_light": [0.168, 0.6, 0.864],
    "color_dodge": [1.0 / 3.0, 1.0, 1.0],
    "color_burn": [0.0, 0.2, 2.0 / 3.0],
}


# ── direct _blend_colors (opaque over opaque collapses to f(cb, cs)) ──────────


@pytest.mark.parametrize("mode", NEW_MODES)
def test_new_mode_centre_pixel_matches_formula(mode: str):
    backdrop = _solid(CB, 1.0)
    source = _solid(CS, 1.0)
    out = _blend_colors(backdrop, source, mode)

    centre = out[CENTRE, CENTRE]
    ref = _ref_blend(mode, np.array(CB), np.array(CS))

    # Cross-check against the independently computed reference formula …
    assert np.allclose(centre[:3], ref, atol=1e-6), (
        f"{mode}: got {centre[:3].tolist()} expected {ref.tolist()}"
    )
    # … and against the exact hand-computed constants.
    assert np.allclose(centre[:3], EXPECTED_CENTRE[mode], atol=1e-6), (
        f"{mode}: got {centre[:3].tolist()} expected {EXPECTED_CENTRE[mode]}"
    )
    # Opaque over opaque keeps alpha == 1.
    assert centre[3] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("mode", ("normal", "multiply", "screen", "overlay"))
def test_original_modes_unchanged(mode: str):
    backdrop = _solid(CB, 1.0)
    source = _solid(CS, 1.0)
    out = _blend_colors(backdrop, source, mode)
    ref = _ref_blend(mode, np.array(CB), np.array(CS))
    assert np.allclose(out[CENTRE, CENTRE, :3], ref, atol=1e-6)
    assert out[CENTRE, CENTRE, 3] == pytest.approx(1.0, abs=1e-6)


def test_unknown_mode_degrades_to_normal_no_crash():
    backdrop = _solid(CB, 1.0)
    source = _solid(CS, 1.0)
    # Must not raise; an unknown blend name behaves like "normal" (source wins).
    out = _blend_colors(backdrop, source, "totally_made_up")
    assert np.allclose(out[CENTRE, CENTRE, :3], CS, atol=1e-6)


# ── end-to-end through the compositor ─────────────────────────────────────────


@pytest.mark.parametrize("mode", NEW_MODES)
def test_new_mode_through_layer_stack(mode: str):
    stack = LayerStack(width=SIZE, height=SIZE, fps=30.0, total_frames=1)
    stack.add_layer(Layer(id="bg", name="bg", z_index=0,
                          content_fn=lambda _i: _solid(CB, 1.0)))
    stack.add_layer(Layer(id="fg", name="fg", z_index=1, blend_mode=mode,
                          content_fn=lambda _i: _solid(CS, 1.0)))
    frame = stack.render_frame(0)
    ref = _ref_blend(mode, np.array(CB), np.array(CS))
    assert np.allclose(frame[CENTRE, CENTRE, :3], ref, atol=1e-6), (
        f"{mode}: compositor gave {frame[CENTRE, CENTRE, :3].tolist()} expected {ref.tolist()}"
    )
    assert frame[CENTRE, CENTRE, 3] == pytest.approx(1.0, abs=1e-6)


# ── _safe_blend whitelist + fallback ──────────────────────────────────────────


@pytest.mark.parametrize("mode", BLEND_MODES)
def test_safe_blend_admits_every_implemented_mode(mode: str):
    assert _safe_blend(mode) == mode


def test_safe_blend_falls_back_to_normal_for_unknown():
    assert _safe_blend("not_a_blend_mode") == "normal"
    assert _safe_blend(None) == "normal"


def test_safe_blend_whitelist_covers_all_new_modes():
    for mode in NEW_MODES:
        assert _safe_blend(mode) == mode
