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
    """Resolve a text layer to a canvas-sized RGBA content_fn.
    
    Supports rich text features:
    - Multiline text (handle 
)
    - Font size control
    - Text alignment (left, center, right)
    - Stroke (outline)
    - Shadow (drop shadow with blur)
    - Background box
    - Line spacing
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont, ImageFilter
    import numpy as np

    props = layer.get("props", {})
    text = str(props.get("text", ""))
    if not text:
        return None

    # Extract text layer properties.
    font_size = int(props.get("font_size", 48))
    font_path = props.get("font_path")
    
    color = props.get("color", "#FFFFFF")
    color_rgba = _parse_color(color)
    
    align = str(props.get("align", "center")).lower()
    if align not in ("left", "center", "right"):
        align = "center"
    
    stroke_config = props.get("stroke")
    stroke_width = 0
    stroke_color_rgba = (0, 0, 0, 1)
    if stroke_config and isinstance(stroke_config, dict):
        stroke_width = int(stroke_config.get("width", 0))
        stroke_color = stroke_config.get("color", "#000000")
        stroke_color_rgba = _parse_color(stroke_color)
    
    shadow_config = props.get("shadow")
    shadow_offset = (0, 0)
    shadow_color_rgba = (0, 0, 0, 0.5)
    shadow_blur = 0
    if shadow_config and isinstance(shadow_config, dict):
        shadow_offset = (
            float(shadow_config.get("dx", 0)),
            float(shadow_config.get("dy", 0))
        )
        shadow_color = shadow_config.get("color", "#000000")
        shadow_color_rgba = _parse_color(shadow_color)
        shadow_blur = float(shadow_config.get("blur", 0))
    
    background = props.get("background")
    background_rgba = None
    if background:
        background_rgba = _parse_color(background)
    
    line_spacing = float(props.get("line_spacing", 1.0))
    
    # Load font.
    try:
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
        else:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    def text_content_fn(_frame_index: int) -> np.ndarray:
        """Render text to canvas-sized RGBA."""
        # Create a temporary large canvas for text layout, then composite.
        temp_w = max(ctx.width * 2, 1024)
        temp_h = max(ctx.height * 2, 1024)
        temp_canvas = PILImage.new("RGBA", (int(temp_w), int(temp_h)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(temp_canvas)
        
        # Split text into lines.
        lines = text.split("\n")
        
        # Compute text block dimensions.
        line_heights = []
        line_widths = []
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
            except Exception:
                w = len(line) * font_size // 2
                h = font_size
            line_widths.append(w)
            line_heights.append(h)
        
        # Compute block size.
        if not line_heights:
            return np.zeros((ctx.height, ctx.width, 4), dtype=np.float32)
        
        block_width = max(line_widths) if line_widths else font_size
        block_height = sum(line_heights) + (len(lines) - 1) * line_spacing * max(line_heights)
        
        # Add padding for stroke.
        padding = stroke_width + 2
        block_width = int(block_width + 2 * padding)
        block_height = int(block_height + 2 * padding)
        
        # Create text block canvas.
        text_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_canvas)
        
        # Draw each line.
        y_offset = padding
        for i, line in enumerate(lines):
            if i > 0:
                y_offset += line_heights[i - 1] * line_spacing
            
            # Compute x position based on alignment.
            if align == "left":
                x_pos = padding
            elif align == "right":
                x_pos = block_width - line_widths[i] - padding
            else:  # center
                x_pos = (block_width - line_widths[i]) / 2
            
            # Draw shadow if configured.
            if shadow_blur > 0 or shadow_offset != (0, 0):
                shadow_x = x_pos + shadow_offset[0]
                shadow_y = y_offset + shadow_offset[1]
                shadow_fill = tuple(
                    int(min(255, max(0, c * 255))) 
                    for c in shadow_color_rgba[:3]
                ) + (255,)
                text_draw.text(
                    (shadow_x, shadow_y), line,
                    fill=shadow_fill, font=font,
                    stroke_width=stroke_width,
                    stroke_fill=shadow_fill if stroke_width > 0 else None
                )
            
            # Draw stroke (outline) and text.
            text_fill = tuple(
                int(min(255, max(0, c * 255))) 
                for c in color_rgba[:3]
            ) + (255,)
            stroke_fill = None
            if stroke_width > 0:
                stroke_fill = tuple(
                    int(min(255, max(0, c * 255))) 
                    for c in stroke_color_rgba[:3]
                ) + (255,)
            
            text_draw.text(
                (x_pos, y_offset), line,
                fill=text_fill, font=font,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill
            )
        
        # Apply shadow blur if needed.
        if shadow_blur > 0:
            # Create shadow layer and blur it.
            shadow_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_canvas)
            y_offset = padding
            for i, line in enumerate(lines):
                if i > 0:
                    y_offset += line_heights[i - 1] * line_spacing
                
                if align == "left":
                    x_pos = padding
                elif align == "right":
                    x_pos = block_width - line_widths[i] - padding
                else:
                    x_pos = (block_width - line_widths[i]) / 2
                
                shadow_x = x_pos + shadow_offset[0]
                shadow_y = y_offset + shadow_offset[1]
                shadow_fill = tuple(
                    int(min(255, max(0, c * 255))) 
                    for c in shadow_color_rgba[:3]
                ) + (255,)
                shadow_draw.text(
                    (shadow_x, shadow_y), line,
                    fill=shadow_fill, font=font,
                    stroke_width=stroke_width,
                    stroke_fill=shadow_fill if stroke_width > 0 else None
                )
            
            # Blur and composite.
            shadow_canvas = shadow_canvas.filter(
                ImageFilter.GaussianBlur(radius=shadow_blur)
            )
            # Composite shadow under text.
            temp = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
            temp.paste(shadow_canvas, (0, 0), shadow_canvas)
            temp.paste(text_canvas, (0, 0), text_canvas)
            text_canvas = temp
        
        # Draw background box if configured.
        if background_rgba:
            bg_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
            bg_draw = ImageDraw.Draw(bg_canvas)
            bg_fill = tuple(
                int(min(255, max(0, c * 255))) 
                for c in background_rgba
            )
            # Background box with slight padding.
            bg_padding = 4
            bg_draw.rectangle(
                [bg_padding, bg_padding, block_width - bg_padding, block_height - bg_padding],
                fill=bg_fill
            )
            # Composite text on top of background.
            bg_canvas.paste(text_canvas, (0, 0), text_canvas)
            text_canvas = bg_canvas
        
        # Centre the text block on the canvas.
        canvas = PILImage.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
        x_pos = (ctx.width - int(block_width)) // 2
        y_pos = (ctx.height - int(block_height)) // 2
        canvas.paste(text_canvas, (x_pos, y_pos), text_canvas)
        
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
