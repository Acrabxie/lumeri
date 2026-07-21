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

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import lumenframe.resolve as resolve_module
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
    def test_positive_bbox_top_does_not_clip_latin_glyphs(self):
        """Latin fonts with a positive top bearing retain every glyph pixel.

        ``ImageDraw.textbbox`` for Helvetica Neue reports a positive ``top``.
        The renderer sizes its temporary canvas from ``bottom - top`` and must
        therefore draw at ``padding - top``; drawing at ``padding`` silently
        clips the bottom of the wordmark.
        """
        font_path = Path("/System/Library/Fonts/HelveticaNeue.ttc")
        if not font_path.is_file():
            pytest.skip("macOS Helvetica Neue collection unavailable")
        text = "Lumeri Video"
        font, _source, _is_tt = _resolve_font(font_path, 76, 500)
        probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
        if bbox[1] <= 0:
            pytest.skip(f"fixture font has no positive bbox top: {bbox}")

        padding = 2
        reference = Image.new(
            "RGBA",
            (bbox[2] - bbox[0] + 2 * padding, bbox[3] - bbox[1] + 2 * padding),
            (0, 0, 0, 0),
        )
        ImageDraw.Draw(reference).text(
            (padding - bbox[0], padding - bbox[1]),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )
        reference_alpha = np.asarray(reference)[:, :, 3]
        ref_rows = np.where(reference_alpha.sum(axis=1) > 0)[0]
        expected_height = int(ref_rows[-1] - ref_rows[0] + 1)
        expected_coverage = int(np.sum(reference_alpha > 0))

        ctx = ResolveContext(width=900, height=300, fps=30, total_frames=1, assets=[])
        frame = _render({
            "text": text,
            "font": str(font_path),
            "font_size": 76,
            "weight": 500,
            "color": "#FFFFFF",
        }, ctx)
        actual_bbox = _alpha_bbox(frame)
        assert actual_bbox is not None
        actual_height = actual_bbox[1] - actual_bbox[0] + 1
        actual_coverage = int(np.sum(frame[:, :, 3] > 0))
        # Alpha compositing in the resolver can quantise a few antialiased edge
        # pixels, but it must preserve the full vertical glyph extent and the
        # overwhelming majority of coverage.
        assert actual_height == expected_height
        assert actual_coverage >= expected_coverage * 0.85

    def test_heterogeneous_multiline_spacing_preserves_last_line(self):
        """A tall first line cannot make a short final line fall off-canvas."""
        font_path = Path("/System/Library/Fonts/HelveticaNeue.ttc")
        if not font_path.is_file():
            pytest.skip("macOS Helvetica Neue collection unavailable")
        lines = ["ÅÉ", "."]
        line_spacing = 1.5
        font, _source, _is_tt = _resolve_font(font_path, 76, 500)
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        heights = [box[3] - box[1] for box in boxes]
        assert heights[0] > heights[1] * 2, heights

        padding = 2
        width = max(box[2] - box[0] for box in boxes) + 2 * padding
        height = int(heights[0] * line_spacing + heights[1] + 2 * padding)
        reference = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        reference_draw = ImageDraw.Draw(reference)
        top = float(padding)
        for index, line in enumerate(lines):
            box = boxes[index]
            reference_draw.text(
                ((width - (box[2] - box[0])) / 2.0, top - box[1]),
                line,
                font=font,
                fill=(255, 255, 255, 255),
            )
            top += heights[index] * line_spacing

        expected_alpha = np.asarray(reference)[:, :, 3]
        expected_coverage = int(np.sum(expected_alpha > 0))
        expected_rows = np.where(expected_alpha.sum(axis=1) > 0)[0]
        expected_height = int(expected_rows[-1] - expected_rows[0] + 1)

        ctx = ResolveContext(width=500, height=300, fps=30, total_frames=1, assets=[])
        frame = _render({
            "text": "\n".join(lines),
            "font": str(font_path),
            "font_size": 76,
            "weight": 500,
            "line_spacing": line_spacing,
            "color": "#FFFFFF",
        }, ctx)
        actual_bbox = _alpha_bbox(frame)
        assert actual_bbox is not None
        assert actual_bbox[1] - actual_bbox[0] + 1 == expected_height
        assert int(np.sum(frame[:, :, 3] > 0)) >= expected_coverage * 0.85

    def test_tight_multiline_spacing_preserves_tall_earlier_line(self):
        """Spacing below 1 cannot size the block only from the short final line."""
        font_path = Path("/System/Library/Fonts/HelveticaNeue.ttc")
        if not font_path.is_file():
            pytest.skip("macOS Helvetica Neue collection unavailable")
        lines = ["ÅÉ", "."]
        line_spacing = 0.8
        font, _source, _is_tt = _resolve_font(font_path, 76, 500)
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        heights = [box[3] - box[1] for box in boxes]
        offsets = [0.0, heights[0] * line_spacing]
        assert offsets[1] + heights[1] < heights[0], (offsets, heights)

        padding = 2
        width = max(box[2] - box[0] for box in boxes) + 2 * padding
        content_height = max(offset + height for offset, height in zip(offsets, heights))
        reference = Image.new(
            "RGBA",
            (width, int(np.ceil(content_height + 2 * padding))),
            (0, 0, 0, 0),
        )
        reference_draw = ImageDraw.Draw(reference)
        for index, line in enumerate(lines):
            box = boxes[index]
            reference_draw.text(
                (
                    (width - (box[2] - box[0])) / 2.0,
                    padding + offsets[index] - box[1],
                ),
                line,
                font=font,
                fill=(255, 255, 255, 255),
            )
        reference_alpha = np.asarray(reference)[:, :, 3]
        expected_rows = np.where(reference_alpha.sum(axis=1) > 0)[0]
        expected_height = int(expected_rows[-1] - expected_rows[0] + 1)
        expected_coverage = int(np.sum(reference_alpha > 0))

        ctx = ResolveContext(width=500, height=300, fps=30, total_frames=1, assets=[])
        frame = _render({
            "text": "\n".join(lines),
            "font": str(font_path),
            "font_size": 76,
            "weight": 500,
            "line_spacing": line_spacing,
            "color": "#FFFFFF",
        }, ctx)
        actual_bbox = _alpha_bbox(frame)
        assert actual_bbox is not None
        assert actual_bbox[1] - actual_bbox[0] + 1 == expected_height
        assert int(np.sum(frame[:, :, 3] > 0)) >= expected_coverage * 0.85

    def test_tracking_shadow_glow_and_core_share_bbox_corrected_y(self, monkeypatch):
        """Every tracked-text draw path uses the same bbox-top correction."""
        font_path = Path("/System/Library/Fonts/HelveticaNeue.ttc")
        if not font_path.is_file():
            pytest.skip("macOS Helvetica Neue collection unavailable")
        text = "Track"
        font, _source, _is_tt = _resolve_font(font_path, 76, 500)
        bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
        assert bbox[1] > 0, bbox

        calls: list[tuple[float, float]] = []
        original = resolve_module._draw_line_spaced

        def spy(draw, xy, *args, **kwargs):
            calls.append((float(xy[0]), float(xy[1])))
            return original(draw, xy, *args, **kwargs)

        monkeypatch.setattr(resolve_module, "_draw_line_spaced", spy)
        glow_radius = 4.0
        shadow_dy = 7.0
        _render({
            "text": text,
            "font": str(font_path),
            "font_size": 76,
            "weight": 500,
            "letter_spacing": 1.5,
            "color": "#FFFFFF",
            "shadow": {"dx": 3.0, "dy": shadow_dy, "color": "#FF00FF", "blur": 0.0},
            "glow": {"radius": glow_radius, "color": "#00FFFF", "intensity": 1.0},
        })
        assert len(calls) == 3, calls
        padding = 2 + int(glow_radius) * 2 + 2
        corrected_y = padding - bbox[1]
        assert calls[0][1] == pytest.approx(corrected_y + shadow_dy)
        assert calls[1][1] == pytest.approx(corrected_y)
        assert calls[2][1] == pytest.approx(corrected_y)

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

    def test_explicit_hiragino_collection_uses_w6_for_bold(self):
        """An explicit macOS CJK TTC must not pin bold text to face zero/W3."""
        hiragino = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
        if not hiragino.is_file():
            pytest.skip("macOS Hiragino Sans GB collection unavailable")
        regular, regular_source, _ = _resolve_font(hiragino, 96, 500)
        bold, bold_source, _ = _resolve_font(hiragino, 96, 900)
        assert regular.getname()[1] == "W3"
        assert bold.getname()[1] == "W6"
        assert regular_source == str(hiragino)
        assert bold_source == f"{hiragino}#index=2"

        ctx = ResolveContext(width=1280, height=320, fps=30, total_frames=1, assets=[])
        regular_frame = _render({
            "text": "理解时间线", "font": str(hiragino), "font_size": 96,
            "color": "#FFFFFF", "weight": 500,
        }, ctx)
        bold_frame = _render({
            "text": "理解时间线", "font": str(hiragino), "font_size": 96,
            "color": "#FFFFFF", "weight": 900,
        }, ctx)
        regular_coverage = int(np.sum(regular_frame[:, :, 3] > 0.5))
        bold_coverage = int(np.sum(bold_frame[:, :, 3] > 0.5))
        assert bold_coverage > regular_coverage * 1.25


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
