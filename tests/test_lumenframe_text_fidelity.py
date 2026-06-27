"""Pixel-level RENDERING FIDELITY tests for the lumenframe text path.

Regression context
------------------
An integration demo showed a title requested at ``font_size=96`` rendering thin
and tiny.  Root cause: ``_text_resolver`` tried a single bundled font name
(``DejaVuSans.ttf``) and, when that name is not resolvable on the host (the
common case on macOS, where Pillow ships no DejaVu), fell straight through to
``ImageFont.load_default()`` — a fixed ~9px bitmap font that IGNORES
``font_size`` entirely.  So every title rendered at the same tiny height
regardless of the requested pixel size.

The fix (``_resolve_font``) walks a list of real, scalable TrueType candidates
so the requested pixel size visibly changes glyph height, while still falling
back to the legacy ``load_default`` path when no scalable face exists anywhere.

These tests assert, at the pixel level, that:
  * a real scalable face actually resolves on this host (``_resolve_font``),
  * larger ``font_size`` produces a MATERIALLY taller glyph bounding box
    (>= 2x going 32 -> 96), proving size is honored,
  * a single rendered glyph has SUBSTANTIAL coverage (not the ~9px default),
  * rendered text stays within the canvas bounds,
  * the legacy ``load_default`` fallback is still reachable and unchanged.

The TTF-specific assertions are guarded: if NO scalable face is resolvable on
the host, those are skipped — but the size-scaling assertion still runs against
whatever font actually resolved (which on a TTF-less host is the legacy bitmap
and would itself fail the 2x check, surfacing the regression rather than hiding
it).  In practice every supported host ships at least one scalable face.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe.compile import ResolveContext
from lumenframe.resolve import default_resolver, _resolve_font


CTX = ResolveContext(width=1280, height=720, fps=30, total_frames=1, assets=[])


def _render(props: dict, ctx: ResolveContext = CTX) -> np.ndarray:
    layer = {"type": "text", "id": "t", "props": props}
    fn = default_resolver(layer, ctx)
    assert fn is not None
    return fn(0)


def _alpha_bbox(frame: np.ndarray, thresh: float = 0.0):
    """(top, bottom, left, right) of nonzero-alpha pixels, or None if empty."""
    alpha = frame[:, :, 3]
    rows = np.where(alpha.sum(axis=1) > thresh)[0]
    cols = np.where(alpha.sum(axis=0) > thresh)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])


def _bbox_height(frame: np.ndarray) -> int:
    bb = _alpha_bbox(frame)
    return 0 if bb is None else (bb[1] - bb[0] + 1)


def _resolved_is_truetype() -> bool:
    _font, _src, is_tt = _resolve_font(None, 64, None)
    return bool(is_tt)


# --------------------------------------------------------------------------- #
# Font resolution: a real scalable face must resolve on supported hosts.
# --------------------------------------------------------------------------- #
class TestFontResolution:
    def test_resolves_a_scalable_truetype(self):
        """``_resolve_font`` returns a real TTF (size-honoring) on this host."""
        font, source, is_truetype = _resolve_font(None, 96, None)
        if not is_truetype:
            pytest.skip(f"no scalable TrueType resolvable on host (got {source})")
        # A scalable face exposes per-size metrics; legacy bitmap does not.
        assert hasattr(font, "size")
        assert source != "load_default"

    def test_resolution_label_reports_source(self):
        """The resolver reports WHICH font resolved (useful for proofs)."""
        _font, source, _is_tt = _resolve_font(None, 48, None)
        assert isinstance(source, str) and source


# --------------------------------------------------------------------------- #
# Core fidelity: font_size must scale glyph height when a TTF is resolved.
# --------------------------------------------------------------------------- #
class TestFontSizeScalesGlyphHeight:
    def test_glyph_bbox_height_scales_with_font_size(self):
        """96px glyph bbox is materially taller (>=2x) than the 32px one.

        This is the direct regression guard: with the old ``load_default``
        fallback both sizes collapsed to ~9px and this ratio was ~1.0.
        """
        small = _render({"text": "Title", "font_size": 32, "color": "#FFFFFF"})
        large = _render({"text": "Title", "font_size": 96, "color": "#FFFFFF"})

        h_small = _bbox_height(small)
        h_large = _bbox_height(large)
        assert h_small > 0 and h_large > 0, (h_small, h_large)
        assert h_large >= 2 * h_small, (
            f"font_size not honored: bbox height 32px={h_small} 96px={h_large} "
            f"(ratio {h_large / max(h_small, 1):.2f}, need >= 2.0)"
        )

    def test_single_glyph_has_substantial_coverage(self):
        """A single big glyph covers far more than the ~9px default font.

        The legacy ``load_default`` 'W' fits in roughly a 9px box (well under
        100 covered pixels). A real 96px face covers hundreds+ of pixels.
        """
        if not _resolved_is_truetype():
            pytest.skip("no scalable TrueType resolvable on host")
        ctx = ResolveContext(width=400, height=400, fps=30, total_frames=1, assets=[])
        frame = _render({"text": "W", "font_size": 96, "color": "#FFFFFF"}, ctx)
        coverage = int(np.sum(frame[:, :, 3] > 0.5))
        assert coverage > 400, f"glyph coverage too small ({coverage}px); size ignored?"

    def test_glyph_is_visibly_tall_at_96(self):
        """At font_size=96 a single glyph spans tens of pixels, not ~9."""
        if not _resolved_is_truetype():
            pytest.skip("no scalable TrueType resolvable on host")
        ctx = ResolveContext(width=400, height=400, fps=30, total_frames=1, assets=[])
        frame = _render({"text": "A", "font_size": 96, "color": "#FFFFFF"}, ctx)
        assert _bbox_height(frame) >= 40

    def test_antialiasing_preserved(self):
        """Edges carry partial-alpha pixels (anti-aliasing intact)."""
        if not _resolved_is_truetype():
            pytest.skip("no scalable TrueType resolvable on host")
        ctx = ResolveContext(width=400, height=400, fps=30, total_frames=1, assets=[])
        frame = _render({"text": "O", "font_size": 96, "color": "#FFFFFF"}, ctx)
        alpha = frame[:, :, 4 - 1]
        partial = np.logical_and(alpha > 0.05, alpha < 0.95)
        assert np.any(partial), "no partial-alpha edge pixels — AA lost"


# --------------------------------------------------------------------------- #
# Text must stay within canvas bounds.
# --------------------------------------------------------------------------- #
class TestTextWithinCanvas:
    def test_rendered_text_within_canvas(self):
        """Glyph alpha never spills outside the canvas array bounds."""
        frame = _render({"text": "Hello World", "font_size": 96, "color": "#FFFFFF"})
        assert frame.shape == (CTX.height, CTX.width, 4)
        bb = _alpha_bbox(frame)
        assert bb is not None
        top, bottom, left, right = bb
        assert 0 <= top <= bottom <= CTX.height - 1
        assert 0 <= left <= right <= CTX.width - 1

    def test_large_text_clamped_to_canvas(self):
        """A very large title still produces a finite, in-range frame."""
        frame = _render({"text": "BIG", "font_size": 200, "color": "#FFFFFF"})
        assert frame.shape == (CTX.height, CTX.width, 4)
        assert np.all(frame >= 0.0) and np.all(frame <= 1.0)


# --------------------------------------------------------------------------- #
# Optional weight prop selects a heavier face without breaking rendering.
# --------------------------------------------------------------------------- #
class TestWeightProp:
    def test_bold_weight_still_renders(self):
        """A bold weight resolves and renders text (coverage > 0)."""
        if not _resolved_is_truetype():
            pytest.skip("no scalable TrueType resolvable on host")
        frame = _render(
            {"text": "Bold", "font_size": 80, "color": "#FFFFFF", "weight": "bold"}
        )
        assert np.sum(frame[:, :, 3] > 0.5) > 0

    def test_bold_is_at_least_as_heavy_as_regular(self):
        """Bold glyphs cover >= the same as regular at identical size.

        Skipped if a distinct bold face isn't available (the resolver then
        reuses the regular face and coverage is equal).
        """
        if not _resolved_is_truetype():
            pytest.skip("no scalable TrueType resolvable on host")
        _rf, reg_src, _ = _resolve_font(None, 80, None)
        _bf, bold_src, _ = _resolve_font(None, 80, "bold")
        if reg_src == bold_src:
            pytest.skip("no distinct bold face on host")
        regular = _render({"text": "M", "font_size": 80, "color": "#FFFFFF"})
        bold = _render(
            {"text": "M", "font_size": 80, "color": "#FFFFFF", "weight": "bold"}
        )
        cov_reg = int(np.sum(regular[:, :, 3] > 0.5))
        cov_bold = int(np.sum(bold[:, :, 3] > 0.5))
        assert cov_bold >= cov_reg, (cov_reg, cov_bold)


# --------------------------------------------------------------------------- #
# Legacy fallback path is still reachable and unchanged.
# --------------------------------------------------------------------------- #
class TestLegacyFallback:
    def test_unresolvable_font_falls_back_without_crashing(self):
        """A bogus explicit font path still yields a usable font.

        With no system TTF it lands on ``load_default``; otherwise the resolver
        recovers onto a system face. Either way rendering must not crash.
        """
        font, source, _is_tt = _resolve_font(
            "/no/such/font/definitely-missing.ttf", 64, None
        )
        assert font is not None
        # Render through the public path with the bogus font to prove no crash.
        frame = _render(
            {"text": "Fallback", "font_size": 64, "color": "#FFFFFF",
             "font": "/no/such/font/definitely-missing.ttf"}
        )
        assert frame.shape == (CTX.height, CTX.width, 4)

    def test_load_default_label_when_no_scalable_face(self):
        """The legacy bitmap path reports the ``load_default`` source label.

        Skipped on hosts that DO have a scalable face (the resolver never needs
        the legacy path there).
        """
        _font, source, is_tt = _resolve_font(None, 64, None)
        if is_tt:
            pytest.skip("host has a scalable face; legacy path not exercised")
        assert source == "load_default"
