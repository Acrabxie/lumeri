"""Default resolver — turns lumenframe layers into canvas-sized RGBA content_fn.

The resolver bridge connects lumenframe's editable document model to real media
(images, videos, text, audio). A resolver produces ``content_fn: int -> RGBAFrame``
for each layer; the compile module wraps these into a compositing stack.

This module provides the ``default_resolver`` which handles:
- ``image`` layers: read asset path -> fit to canvas (centred)
- ``video`` layers: read frame at index -> fit to canvas
- ``text`` layers: render text to transparent RGBA canvas
- ``audio`` layers: no visual content (returns None)
- ``solid``/``composition``: handled by compile.py directly
- missing resources: strict mode raises; otherwise skips gracefully
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from lumenframe.compile import CompileError, ContentFn, ResolveContext


def default_resolver(
    layer: dict[str, Any],
    ctx: ResolveContext,
) -> Optional[ContentFn]:
    """Resolve a layer to a canvas-sized RGBA content_fn.

    Handles image, video, text, and audio layers. For unknown types or missing
    resources, returns None in non-strict mode; raises CompileError in strict.

    Args:
        layer: The lumenframe layer dict (type, asset_id, props, etc.)
        ctx: ResolveContext with canvas size, fps, total frames, assets list.

    Returns:
        A callable(frame_index: int) -> np.ndarray [H, W, 4] in float32 [0, 1].
        Or None if the layer has no visual content (audio, null, etc.).

    Raises:
        CompileError: if called from compile_to_layer_stack(strict=True)
            and the layer cannot be resolved.
    """
    ltype = str(layer.get("type", ""))

    if ltype == "audio":
        # Audio layers have no visual content.
        return None

    if ltype == "image":
        return _image_resolver(layer, ctx)

    if ltype == "video":
        return _video_resolver(layer, ctx)

    if ltype == "text":
        return _text_resolver(layer, ctx)

    # Unknown type — extension or future layer type
    return None


def _image_resolver(layer: dict[str, Any], ctx: ResolveContext) -> Optional[ContentFn]:
    """Resolve an image layer to a canvas-sized RGBA content_fn."""
    from gemia.video.layers import _read_image_rgba, _fit_to_canvas

    asset_id = layer.get("asset_id")
    asset = ctx.asset(asset_id) if asset_id else None
    if not asset:
        return None

    path = asset.get("path")
    if not path:
        return None

    try:
        img_rgba = _read_image_rgba(path)
    except Exception:
        return None

    # Position image on canvas (centred by default, respecting transform).
    # The transform will be applied by compile.py, so here we just produce
    # canvas-sized content with the image fitted.
    h, w = img_rgba.shape[:2]
    canvas_h, canvas_w = ctx.height, ctx.width

    # Centre the image: if it's smaller, centre it; if larger, crop to centre.
    y_offset = (canvas_h - h) // 2
    x_offset = (canvas_w - w) // 2

    fitted = _fit_to_canvas(img_rgba, canvas_w, canvas_h, (x_offset, y_offset))

    def image_content_fn(_frame_index: int) -> np.ndarray:
        return fitted.copy()

    return image_content_fn


def _video_resolver(layer: dict[str, Any], ctx: ResolveContext) -> Optional[ContentFn]:
    """Resolve a video layer to a canvas-sized RGBA content_fn."""
    from gemia.video.layers import _read_video_frame, _fit_to_canvas

    asset_id = layer.get("asset_id")
    asset = ctx.asset(asset_id) if asset_id else None
    if not asset:
        return None

    path = asset.get("path")
    if not path:
        return None

    # Account for source_in / source_out (trimming within the video).
    source_in = float(layer.get("source_in", 0.0))
    source_out = float(layer.get("source_out", 0.0))
    speed = float(layer.get("speed", 1.0))

    # If source_out is 0, compute it from the layer's duration.
    if source_out <= source_in:
        duration = float(layer.get("duration", 0.0))
        source_out = source_in + duration

    def video_content_fn(frame_index: int) -> np.ndarray:
        """Read video frame, handling trimming, speed, and canvas fit."""
        # Time in seconds = frame_index / fps
        local_time = float(frame_index) / ctx.fps
        # Source time = source_in + local_time * speed
        source_time = source_in + (local_time * speed)
        # Mapped to source video frame.
        source_frame = int(source_time * ctx.fps)

        # Clamp to source range.
        source_frame_min = int(source_in * ctx.fps)
        source_frame_max = int(source_out * ctx.fps)
        source_frame = min(max(source_frame, source_frame_min), source_frame_max - 1)

        try:
            frame_rgba = _read_video_frame(path, source_frame)
        except Exception:
            # Video frame read failed; return transparent canvas.
            return np.zeros((ctx.height, ctx.width, 4), dtype=np.float32)

        # Fit to canvas, centred.
        h, w = frame_rgba.shape[:2]
        canvas_h, canvas_w = ctx.height, ctx.width
        y_offset = (canvas_h - h) // 2
        x_offset = (canvas_w - w) // 2
        return _fit_to_canvas(frame_rgba, canvas_w, canvas_h, (x_offset, y_offset))

    return video_content_fn


def _text_resolver(layer: dict[str, Any], ctx: ResolveContext) -> Optional[ContentFn]:
    """Resolve a text layer to a canvas-sized RGBA content_fn."""
    from PIL import Image as PILImage, ImageDraw, ImageFont
    import numpy as np

    props = layer.get("props", {})
    text = str(props.get("text", ""))
    if not text:
        return None

    # Extract text layer properties.
    font_config = props.get("font") or {}
    font_size = int(font_config.get("size", 48))

    color = props.get("color", "#FFFFFF")
    # Parse color: expect "#RRGGBB" or "#RRGGBBAA".
    color_rgba = _parse_color(color)

    # Load font.
    try:
        if font_config.get("path"):
            font = ImageFont.truetype(font_config.get("path"), font_size)
        else:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    def text_content_fn(_frame_index: int) -> np.ndarray:
        """Render text to canvas-sized RGBA."""
        # Create canvas.
        canvas = PILImage.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # Get text bounding box to centre it.
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w, text_h = len(text) * font_size // 2, font_size

        # Centre position.
        x = (ctx.width - text_w) // 2
        y = (ctx.height - text_h) // 2

        # Convert color to 0-255 range. Force alpha=255 so layer opacity controls transparency.
        fill = tuple(int(min(255, max(0, c * 255))) for c in color_rgba[:3]) + (255,)

        # Draw text.
        draw.text((x, y), text, fill=fill, font=font)

        # Convert to float32 RGBA in [0, 1].
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        return arr

    return text_content_fn


def _parse_color(color: Any) -> tuple[float, float, float, float]:
    """Parse color to RGBA float tuple in [0, 1]."""
    if isinstance(color, str) and color.startswith("#"):
        hexs = color[1:]
        if len(hexs) in (6, 8):
            r = int(hexs[0:2], 16) / 255.0
            g = int(hexs[2:4], 16) / 255.0
            b = int(hexs[4:6], 16) / 255.0
            a = int(hexs[6:8], 16) / 255.0 if len(hexs) == 8 else 1.0
            return (r, g, b, a)
    if isinstance(color, (list, tuple)) and len(color) in (3, 4):
        vals = [float(v) for v in color]
        if max(vals) > 1.0:
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)  # type: ignore[return-value]
    return (1.0, 1.0, 1.0, 1.0)  # white


# Re-export for convenience.
__all__ = ["default_resolver"]
