"""Pixel-level tests for the NEW CapCut text features in lumenframe.resolve.

The base rich-text features (multiline / align / stroke / shadow / background /
line_spacing / font_size / color) already existed and are covered by
``test_lumenframe_textstyle_render.py``. This module covers the features added
on the ``overnight/r4-resolve-text`` branch:

- Letter-spacing / tracking (CapCut "字间距")  -> wider glyph advance
- Vertical gradient fill (CapCut "渐变")       -> top hue != bottom hue
- Outer glow (CapCut "发光")                   -> soft alpha halo outside glyphs

Each new feature ships a focused pixel assertion. A golden no-op test proves
that when the new props are ABSENT the rendered frame is byte-identical to the
output produced without those props (the resolver is purely additive).
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe.compile import ResolveContext
from lumenframe.resolve import default_resolver, _line_advance


CTX = ResolveContext(width=640, height=360, fps=30, total_frames=30, assets=[])


def _render(props: dict) -> np.ndarray:
    layer = {"type": "text", "id": "t", "props": props}
    fn = default_resolver(layer, CTX)
    assert fn is not None
    return fn(0)


# --------------------------------------------------------------------------- #
# Golden no-op: absence of the new props == prior rendering, byte for byte.
# --------------------------------------------------------------------------- #
class TestAdditiveNoOp:
    """New features must not change rendering when their props are absent."""

    @pytest.mark.parametrize(
        "props",
        [
            {"text": "Hello", "font_size": 48, "color": "#FF0000"},
            {
                "text": "A\nBB",
                "font_size": 40,
                "color": "#00FF00",
                "align": "left",
                "stroke": {"color": "#000000", "width": 3},
                "shadow": {"color": "#000000", "dx": 4, "dy": 4, "blur": 3},
                "background": "#0000FFFF",
                "line_spacing": 1.4,
            },
        ],
    )
    def test_absent_props_are_noop(self, props):
        """Rendering with the bare props == rendering with explicit no-op props.

        Setting letter_spacing=0 and omitting gradient/glow must reproduce the
        exact same pixels as not mentioning the new keys at all.
        """
        baseline = _render(dict(props))
        with_inert = _render({**props, "letter_spacing": 0.0})
        assert np.array_equal(baseline, with_inert)
        # gradient / glow with falsy configs are also inert
        with_inert2 = _render({**props, "gradient": None, "glow": None})
        assert np.array_equal(baseline, with_inert2)

    def test_empty_gradient_dict_is_noop(self):
        """A gradient dict missing from/to does nothing."""
        base = _render({"text": "Hi", "font_size": 64, "color": "#FFFFFF"})
        gone = _render(
            {"text": "Hi", "font_size": 64, "color": "#FFFFFF", "gradient": {}}
        )
        assert np.array_equal(base, gone)

    def test_zero_radius_glow_is_noop(self):
        """A glow with radius 0 does nothing."""
        base = _render({"text": "Hi", "font_size": 64, "color": "#FFFFFF"})
        gone = _render(
            {
                "text": "Hi",
                "font_size": 64,
                "color": "#FFFFFF",
                "glow": {"color": "#00FF00", "radius": 0},
            }
        )
        assert np.array_equal(base, gone)


# --------------------------------------------------------------------------- #
# Vertical gradient fill
# --------------------------------------------------------------------------- #
class TestGradientFill:
    def test_top_hue_differs_from_bottom_hue(self):
        """Vertical gradient: top glyph rows are redder, bottom rows bluer."""
        frame = _render(
            {
                "text": "Hi",
                "font_size": 64,
                "color": "#FFFFFF",
                "gradient": {"from": "#FF0000", "to": "#0000FF"},
            }
        )
        alpha = frame[:, :, 3]
        rows = np.where(np.any(alpha > 0.5, axis=1))[0]
        assert len(rows) > 1, "need at least two glyph rows to measure a gradient"
        top, bot = rows[0] + 1, rows[-1] - 1

        def avg_rgb(row: int) -> np.ndarray:
            sel = alpha[row] > 0.5
            return frame[row, sel, :3].mean(axis=0)

        top_rgb, bot_rgb = avg_rgb(top), avg_rgb(bot)
        # Top should carry more red than the bottom; bottom more blue than top.
        assert top_rgb[0] > bot_rgb[0] + 0.1, (top_rgb, bot_rgb)
        assert bot_rgb[2] > top_rgb[2] + 0.1, (top_rgb, bot_rgb)
        # And the two rows are genuinely different hues (not a flat fill).
        assert not np.allclose(top_rgb, bot_rgb, atol=0.1)

    def test_gradient_preserves_glyph_alpha(self):
        """Gradient only recolors; the glyph alpha/coverage is unchanged."""
        plain = _render({"text": "Hi", "font_size": 64, "color": "#FFFFFF"})
        grad = _render(
            {
                "text": "Hi",
                "font_size": 64,
                "color": "#FFFFFF",
                "gradient": {"from": "#FF0000", "to": "#0000FF"},
            }
        )
        # Same block geometry (no extra padding for gradient) => same alpha map.
        assert np.array_equal(plain[:, :, 3], grad[:, :, 3])


# --------------------------------------------------------------------------- #
# Outer glow
# --------------------------------------------------------------------------- #
class TestOuterGlow:
    def test_glow_adds_soft_halo_outside_glyph(self):
        """Glow produces partial-alpha pixels extending beyond the solid core."""
        frame = _render(
            {
                "text": "Hi",
                "font_size": 64,
                "color": "#FFFFFF",
                "glow": {"color": "#00FFFF", "radius": 4, "intensity": 4.0},
            }
        )
        alpha = frame[:, :, 3]
        core = alpha > 0.5
        soft = (alpha > 0.02) & (alpha <= 0.5)
        assert core.any(), "glyph core must render"
        # Glow must produce soft partial-alpha pixels. (Font-agnostic: the earlier
        # `soft.sum() > core.sum()` only held for the broken ~9px bitmap default
        # font whose glyphs were nearly all edge; with a real scalable TTF the
        # filled glyph interior legitimately exceeds the thin halo rim. The real
        # glow guarantees are asserted: soft pixels exist AND the lit region
        # extends beyond the glyph bbox below.)
        assert soft.sum() > 0, "glow halo should add soft (partial-alpha) pixels"

        cxs = np.where(core)[1]
        all_xs = np.where(alpha > 0.02)[1]
        # The halo extends the lit region beyond the solid glyph bbox.
        assert all_xs.min() < cxs.min()
        assert all_xs.max() > cxs.max()

    def test_no_glow_has_no_wide_halo(self):
        """Without glow, lit pixels do not extend beyond the glyph bbox."""
        frame = _render({"text": "Hi", "font_size": 64, "color": "#FFFFFF"})
        alpha = frame[:, :, 3]
        cxs = np.where(alpha > 0.5)[1]
        all_xs = np.where(alpha > 0.02)[1]
        # Antialiasing may add at most a 1px fringe; assert it stays tight.
        assert all_xs.min() >= cxs.min() - 1
        assert all_xs.max() <= cxs.max() + 1

    def test_glow_carries_its_color(self):
        """Soft-halo pixels carry the glow color, not the white glyph color."""
        frame = _render(
            {
                "text": "Hi",
                "font_size": 64,
                "color": "#FFFFFF",
                "glow": {"color": "#00FF00", "radius": 4, "intensity": 4.0},
            }
        )
        alpha = frame[:, :, 3]
        soft = (alpha > 0.02) & (alpha < 0.4)
        assert soft.any()
        # In the soft region the green channel dominates red+blue (glow is green).
        g = frame[:, :, 1][soft]
        r = frame[:, :, 0][soft]
        b = frame[:, :, 2][soft]
        assert g.sum() > r.sum()
        assert g.sum() > b.sum()


# --------------------------------------------------------------------------- #
# Letter-spacing / tracking
# --------------------------------------------------------------------------- #
class TestLetterSpacing:
    def test_tracking_widens_glyph_advance(self):
        """Positive letter_spacing pushes the 2nd glyph right -> wider span."""
        normal = _render(
            {"text": "II", "font_size": 80, "color": "#FFFFFF", "align": "left"}
        )
        tracked = _render(
            {
                "text": "II",
                "font_size": 80,
                "color": "#FFFFFF",
                "align": "left",
                "letter_spacing": 40,
            }
        )

        def span(frame: np.ndarray) -> int:
            cols = np.where(np.any(frame[:, :, 3] > 0.4, axis=0))[0]
            return int(cols[-1] - cols[0])

        normal_span = span(normal)
        tracked_span = span(tracked)
        assert tracked_span > normal_span + 10, (normal_span, tracked_span)

    def test_line_advance_helper_monotonic(self):
        """_line_advance grows with letter_spacing and with more glyphs."""
        from PIL import ImageFont

        font = ImageFont.load_default()
        a0 = _line_advance(font, "AB", 0.0)
        a1 = _line_advance(font, "AB", 20.0)
        assert a1 > a0
        # exactly one inter-glyph gap of 20 for a 2-char string
        assert abs((a1 - a0) - 20.0) < 0.01
        # single glyph has no trailing gap regardless of spacing
        s0 = _line_advance(font, "A", 0.0)
        s1 = _line_advance(font, "A", 50.0)
        assert abs(s1 - s0) < 0.01

    def test_negative_tracking_tightens(self):
        """Negative letter_spacing pulls glyphs together (narrower span)."""
        normal = _render(
            {"text": "WW", "font_size": 80, "color": "#FFFFFF", "align": "left"}
        )
        tight = _render(
            {
                "text": "WW",
                "font_size": 80,
                "color": "#FFFFFF",
                "align": "left",
                "letter_spacing": -3,
            }
        )

        def span(frame: np.ndarray) -> int:
            cols = np.where(np.any(frame[:, :, 3] > 0.4, axis=0))[0]
            return int(cols[-1] - cols[0])

        assert span(tight) <= span(normal)


# --------------------------------------------------------------------------- #
# Features compose without crashing and stay in-range.
# --------------------------------------------------------------------------- #
class TestFeatureComposition:
    def test_all_new_features_together(self):
        """Gradient + glow + tracking + existing stroke/shadow render cleanly."""
        frame = _render(
            {
                "text": "GO\nGO",
                "font_size": 56,
                "color": "#FFFFFF",
                "align": "center",
                "stroke": {"color": "#000000", "width": 2},
                "shadow": {"color": "#000000", "dx": 3, "dy": 3, "blur": 2},
                "letter_spacing": 8,
                "gradient": {"from": "#FFEE00", "to": "#FF0066"},
                "glow": {"color": "#FF00FF", "radius": 3, "intensity": 3.0},
            }
        )
        assert frame.shape == (360, 640, 4)
        assert frame.dtype == np.float32
        assert np.all(frame >= 0.0) and np.all(frame <= 1.0)
        assert np.any(frame[:, :, 3] > 0.1)
