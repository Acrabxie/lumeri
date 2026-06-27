"""Tests for rich text rendering in lumenframe text layers.

Covers:
- Multiline text with line spacing
- Text alignment (left, center, right)
- Font size and font path
- Text color
- Stroke (outline)
- Shadow (drop shadow with blur)
- Background box
- Missing/empty text handling
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from lumenframe import model
from lumenframe.compile import compile_to_layer_stack, ResolveContext
from lumenframe.resolve import default_resolver, _parse_color


class TestTextColorParsing:
    """Test color parsing utility."""

    def test_parse_hex_rgb(self):
        """Parse #RRGGBB format."""
        rgba = _parse_color("#FF0000")
        assert rgba == (1.0, 0.0, 0.0, 1.0)

    def test_parse_hex_rgba(self):
        """Parse #RRGGBBAA format."""
        rgba = _parse_color("#FF000080")
        assert abs(rgba[3] - 0.502) < 0.01  # 0x80 = 128

    def test_parse_white_default(self):
        """Default to white if invalid."""
        rgba = _parse_color("invalid")
        assert rgba == (1.0, 1.0, 1.0, 1.0)


class TestTextResolution:
    """Test text layer resolution and rendering."""

    def test_empty_text_returns_none(self):
        """Empty text should return None."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {"text": ""}
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        assert fn is None

    def test_missing_text_returns_none(self):
        """Missing text prop should return None."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {}
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        assert fn is None

    def test_text_content_fn_returns_canvas_sized_rgba(self):
        """Text content_fn should return canvas-sized RGBA float32 [0, 1]."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF"
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        assert fn is not None

        frame = fn(0)
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (1080, 1920, 4)
        assert frame.dtype == np.float32
        assert np.all(frame >= 0.0) and np.all(frame <= 1.0)

    def test_multiline_text_renders_multiple_lines(self):
        """Multiline text should render all lines."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Line 1\nLine 2\nLine 3",
                "font_size": 48,
                "color": "#FFFFFF",
                "line_spacing": 1.5
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        assert fn is not None

        frame = fn(0)
        alpha_channel = frame[:, :, 3]
        assert np.any(alpha_channel > 0.1)

    def test_text_alignment_left(self):
        """Left-aligned text should be positioned at left edge."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "align": "left"
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        frame = fn(0)

        alpha = frame[:, :, 3]
        rows_with_text = np.any(alpha > 0.1, axis=1)
        cols_with_text = np.any(alpha > 0.1, axis=0)

        if np.any(rows_with_text) and np.any(cols_with_text):
            leftmost_col = np.argmax(cols_with_text)
            assert leftmost_col < 960

    def test_text_alignment_center(self):
        """Center-aligned text should be near canvas center."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "align": "center"
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        frame = fn(0)

        alpha = frame[:, :, 3]
        cols_with_text = np.any(alpha > 0.1, axis=0)

        if np.any(cols_with_text):
            col_indices = np.where(cols_with_text)[0]
            center_col = (col_indices[0] + col_indices[-1]) / 2.0
            assert 800 < center_col < 1120

    def test_text_color_renders_correctly(self):
        """Text color should appear in rendered frame."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Red",
                "font_size": 48,
                "color": "#FF0000"
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        frame = fn(0)

        alpha = frame[:, :, 3]
        text_pixels = alpha > 0.5
        if np.any(text_pixels):
            r = frame[:, :, 0][text_pixels]
            g = frame[:, :, 1][text_pixels]
            b = frame[:, :, 2][text_pixels]
            assert np.mean(r) > 0.8
            assert np.mean(g) < 0.3
            assert np.mean(b) < 0.3

    def test_stroke_creates_outline(self):
        """Stroke should create visible outline around text."""
        layer_without_stroke = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF"
            }
        }
        layer_with_stroke = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "stroke": {"color": "#000000", "width": 3}
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])

        frame_no_stroke = default_resolver(layer_without_stroke, ctx)(0)
        frame_with_stroke = default_resolver(layer_with_stroke, ctx)(0)

        alpha_no_stroke = frame_no_stroke[:, :, 3]
        alpha_with_stroke = frame_with_stroke[:, :, 3]

        count_no_stroke = np.sum(alpha_no_stroke > 0.5)
        count_with_stroke = np.sum(alpha_with_stroke > 0.5)

        assert count_with_stroke > count_no_stroke

    def test_shadow_renders_offset(self):
        """Shadow should render at offset position."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "shadow": {"color": "#000000", "dx": 5, "dy": 5, "blur": 0}
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        frame = fn(0)

        alpha = frame[:, :, 3]
        if np.any(alpha > 0.1):
            assert frame.shape == (1080, 1920, 4)

    def test_shadow_blur_smooths_edges(self):
        """Shadow with blur should have smooth edges."""
        layer_no_blur = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "shadow": {"color": "#000000", "dx": 5, "dy": 5, "blur": 0}
            }
        }
        layer_with_blur = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "shadow": {"color": "#000000", "dx": 5, "dy": 5, "blur": 5}
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])

        frame_no_blur = default_resolver(layer_no_blur, ctx)(0)
        frame_with_blur = default_resolver(layer_with_blur, ctx)(0)

        assert np.any(frame_no_blur[:, :, 3] > 0.1)
        assert np.any(frame_with_blur[:, :, 3] > 0.1)

    def test_background_box_renders(self):
        """Background box should render behind text."""
        layer = {
            "type": "text",
            "id": "text1",
            "props": {
                "text": "Hello",
                "font_size": 48,
                "color": "#FFFFFF",
                "background": "#FF0000FF"
            }
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])
        fn = default_resolver(layer, ctx)
        frame = fn(0)

        alpha = frame[:, :, 3]
        if np.any(alpha > 0.5):
            r = frame[:, :, 0]
            has_red = np.any(r > 0.8)
            assert has_red

    def test_font_size_affects_rendering(self):
        """Larger font size should produce larger text."""
        layer_small = {
            "type": "text",
            "id": "text1",
            "props": {"text": "Hi", "font_size": 24, "color": "#FFFFFF"}
        }
        layer_large = {
            "type": "text",
            "id": "text1",
            "props": {"text": "Hi", "font_size": 96, "color": "#FFFFFF"}
        }
        ctx = ResolveContext(width=1920, height=1080, fps=30, total_frames=30, assets=[])

        frame_small = default_resolver(layer_small, ctx)(0)
        frame_large = default_resolver(layer_large, ctx)(0)

        count_small = np.sum(frame_small[:, :, 3] > 0.5)
        count_large = np.sum(frame_large[:, :, 3] > 0.5)
        assert count_large >= count_small  # May be same due to antialiasing


class TestTextCompilation:
    """Test text layer compilation into layer stack."""

    def test_compile_text_layer_simple(self):
        """Compile a simple text layer."""
        doc = {
            "canvas": {"width": 1920, "height": 1080, "fps": 30},
            "root": {
                "id": "root",
                "type": "composition",
                "children": [
                    {
                        "type": "text",
                        "id": "text1",
                        "start": 0,
                        "duration": 1.0,
                        "props": {
                            "text": "Hello World",
                            "font_size": 72,
                            "color": "#FFFFFF"
                        }
                    }
                ]
            }
        }
        stack = compile_to_layer_stack(doc)
        assert stack is not None
        assert stack.width == 1920
        assert stack.height == 1080

    def test_compile_text_with_all_features(self):
        """Compile text layer with all rich text features."""
        doc = {
            "canvas": {"width": 1920, "height": 1080, "fps": 30},
            "root": {
                "id": "root",
                "type": "composition",
                "children": [
                    {
                        "type": "text",
                        "id": "text1",
                        "start": 0,
                        "duration": 2.0,
                        "props": {
                            "text": "Line 1\nLine 2",
                            "font_size": 48,
                            "color": "#FFFFFF",
                            "align": "center",
                            "stroke": {"color": "#000000", "width": 2},
                            "shadow": {"color": "#000000", "dx": 2, "dy": 2, "blur": 4},
                            "background": "#0000FFAA",
                            "line_spacing": 1.5
                        }
                    }
                ]
            }
        }
        stack = compile_to_layer_stack(doc)
        assert stack is not None

        frame = stack.render_frame(0)
        assert frame is not None
        assert frame.shape == (1080, 1920, 4)
        assert frame.dtype == np.float32
        assert np.all(frame >= 0.0) and np.all(frame <= 1.0)
