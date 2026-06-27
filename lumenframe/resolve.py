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

    if ltype == "html":
        # HTML/CSS/JS motion-graphics layer: renders to an mp4 (cached) and is
        # then sampled through _video_resolver, so it composites like a video.
        from lumenframe.resolve_html import html_resolver
        return html_resolver(layer, ctx)

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


def _draw_line_spaced(draw, xy, line, font, fill, letter_spacing, stroke_width=0, stroke_fill=None):
    """Draw a line of text, inserting ``letter_spacing`` px between glyphs.

    Mirrors ``ImageDraw.text`` when ``letter_spacing`` is 0 by delegating to a
    single ``draw.text`` call, so non-tracked rendering is byte-identical. When
    tracking is requested, glyphs are drawn one at a time and advanced by their
    individual width plus ``letter_spacing``.
    """
    x, y = xy
    for ch in line:
        draw.text(
            (x, y), ch, fill=fill, font=font,
            stroke_width=stroke_width if stroke_width > 0 else 0,
            stroke_fill=stroke_fill,
        )
        try:
            adv = draw.textlength(ch, font=font)
        except Exception:
            adv = font.getbbox(ch)[2] if hasattr(font, "getbbox") else len(ch) * 8
        x += adv + letter_spacing


def _line_advance(font, line, letter_spacing):
    """Pixel advance of a line including per-glyph ``letter_spacing``."""
    from PIL import Image as _PILImage, ImageDraw as _ImageDraw

    tmp = _ImageDraw.Draw(_PILImage.new("RGBA", (1, 1)))
    total = 0.0
    for ch in line:
        try:
            total += tmp.textlength(ch, font=font)
        except Exception:
            total += font.getbbox(ch)[2] if hasattr(font, "getbbox") else len(ch) * 8
        total += letter_spacing
    if line:
        total -= letter_spacing  # no trailing gap after last glyph
    return total


# --------------------------------------------------------------------------- #
# Font resolution.
#
# RENDERING FIDELITY FIX: the previous text path tried a single bundled font
# name (``DejaVuSans.ttf``) and, when that name is not resolvable on the host
# (common on macOS, where Pillow ships no DejaVu), fell straight through to
# ``ImageFont.load_default()``.  The legacy ``load_default()`` returns a fixed
# ~9px bitmap font that IGNORES ``font_size`` entirely — so a title requested at
# ``font_size=96`` rendered thin and tiny.  ``_resolve_font`` walks a list of
# real, scalable TrueType candidates (explicit prop first, then common system
# fonts, then an optionally-bundled asset, then Pillow's DejaVu name) so the
# requested pixel size visibly changes glyph height.  Only when NOTHING is
# resolvable do we fall back to the legacy ``load_default()`` path, preserving
# the historical fallback behaviour byte-for-byte.
# --------------------------------------------------------------------------- #

# Common scalable TrueType candidates by family, in preference order. Each is a
# (regular_path_or_name, bold_path_or_name) pair so an optional ``weight`` prop
# can pick a heavier face. Names without a directory are resolved by Pillow's
# own font search (it bundles ``DejaVuSans.ttf``).
_SYSTEM_FONT_CANDIDATES = [
    # macOS — guaranteed-present scalable faces.
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    # Linux — DejaVu / Liberation are the usual headless-CI defaults.
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    # Pillow ships DejaVuSans under this bare name; works when the wheel
    # includes it (resolved via Pillow's internal font directory).
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
]

# Directory for an optionally-bundled, permissively-licensed fallback font.
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _is_bold_weight(weight: Any) -> bool:
    """True when ``weight`` requests a bold face (numeric >=600 or a bold name)."""
    if weight is None:
        return False
    try:
        return float(weight) >= 600.0
    except (TypeError, ValueError):
        return str(weight).strip().lower() in {
            "bold", "semibold", "semi-bold", "heavy", "black", "extrabold", "extra-bold",
        }


def _resolve_font(font_path, font_size: int, weight: Any = None):
    """Resolve a scalable TrueType font honoring ``font_size`` in pixels.

    Returns ``(font, source, is_truetype)`` where ``source`` is a human-readable
    label of what resolved (for proofs/debugging) and ``is_truetype`` is True
    when a real scalable face loaded (size affects glyph height).

    Order of preference:
      1. Explicit ``font_path`` prop (a real path or a Pillow-resolvable name),
         trying a bold sibling first when ``weight`` is bold.
      2. Common system fonts (macOS / Linux), weight-aware.
      3. A bundled font under ``lumenframe/assets/`` (if the operator added one).
      4. Pillow's bundled ``DejaVuSans.ttf`` by name.
      5. Legacy ``ImageFont.load_default()`` — fixed ~9px bitmap, size ignored.
         Reached ONLY when no scalable face exists, preserving prior behaviour.
    """
    from PIL import ImageFont

    bold = _is_bold_weight(weight)
    size = int(font_size)

    def _try(name_or_path):
        if not name_or_path:
            return None
        try:
            return ImageFont.truetype(str(name_or_path), size)
        except Exception:
            return None

    # 1) Explicit prop wins. Honor bold by probing common sibling filenames.
    if font_path:
        if bold:
            base = str(font_path)
            for cand in (base.replace(".ttf", " Bold.ttf"),
                         base.replace(".ttf", "-Bold.ttf"),
                         base.replace(".ttf", "bd.ttf")):
                if cand != base:
                    f = _try(cand)
                    if f is not None:
                        return f, cand, True
        f = _try(font_path)
        if f is not None:
            return f, str(font_path), True
        # Explicit font unresolvable: fall through to system candidates.

    # 2) System candidates, weight-aware.
    for regular, bold_path in _SYSTEM_FONT_CANDIDATES:
        chosen = bold_path if bold else regular
        f = _try(chosen)
        if f is not None:
            return f, chosen, True
        # If the bold sibling is missing, accept the regular face.
        if bold:
            f = _try(regular)
            if f is not None:
                return f, regular, True

    # 3) Optional bundled asset (any .ttf/.otf the operator dropped in assets/).
    try:
        if _ASSETS_DIR.is_dir():
            for ext in ("*.ttf", "*.otf"):
                for asset in sorted(_ASSETS_DIR.glob(ext)):
                    f = _try(asset)
                    if f is not None:
                        return f, str(asset), True
    except Exception:
        pass

    # 4) Pillow's bundled DejaVu by bare name (redundant with #2 but cheap).
    f = _try("DejaVuSans.ttf")
    if f is not None:
        return f, "DejaVuSans.ttf", True

    # 5) Legacy fixed-size bitmap fallback (size ignored) — unchanged behaviour.
    return ImageFont.load_default(), "load_default", False


def _text_resolver(layer: dict[str, Any], ctx: ResolveContext) -> Optional[ContentFn]:
    """Resolve a text layer to a canvas-sized RGBA content_fn.

    Supports rich text features:
    - Multiline text (handle \\n)
    - Font size control
    - Text alignment (left, center, right)
    - Stroke (outline)
    - Shadow (drop shadow with blur)
    - Background box
    - Line spacing
    - Letter-spacing / tracking (CapCut "字间距")
    - Vertical gradient fill (CapCut "渐变")
    - Outer glow (CapCut "发光")
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont, ImageFilter
    import numpy as np

    props = layer.get("props", {})
    text = str(props.get("text", ""))
    if not text:
        return None

    # Extract text layer properties.
    font_size = int(props.get("font_size", 48))
    font_path = props.get("font")  # use "font" not "font_path" per spec
    font_weight = props.get("weight")  # optional: "bold" / numeric (>=600 == bold)

    color = props.get("color", "#FFFFFF")
    color_rgba = _parse_color(color)

    align = str(props.get("align", "center")).lower()
    if align not in ("left", "center", "right"):
        align = "center"

    stroke_config = props.get("stroke")
    stroke_width = 0
    stroke_color_rgba = (0, 0, 0, 1)
    if stroke_config and isinstance(stroke_config, dict):
        stroke_width = int(float(stroke_config.get("width", 0)))
        stroke_color = stroke_config.get("color", "#000000")
        stroke_color_rgba = _parse_color(stroke_color)

    shadow_config = props.get("shadow")
    shadow_offset = (0.0, 0.0)
    shadow_color_rgba = (0, 0, 0, 0.5)
    shadow_blur = 0.0
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

    # --- New CapCut text features (ADD-only; inactive when prop absent) ---

    # Letter-spacing / tracking: extra px inserted between glyphs.
    letter_spacing = float(props.get("letter_spacing", 0.0))
    use_tracking = letter_spacing != 0.0

    # Vertical gradient fill: {"from": "#RRGGBB", "to": "#RRGGBB"} top->bottom.
    gradient_config = props.get("gradient")
    gradient_from_rgba = None
    gradient_to_rgba = None
    if gradient_config and isinstance(gradient_config, dict):
        g_from = gradient_config.get("from")
        g_to = gradient_config.get("to")
        if g_from is not None and g_to is not None:
            gradient_from_rgba = _parse_color(g_from)
            gradient_to_rgba = _parse_color(g_to)
    use_gradient = gradient_from_rgba is not None and gradient_to_rgba is not None

    # Outer glow: {"color": "#RRGGBB", "radius": px, "intensity": 0..N}.
    glow_config = props.get("glow")
    glow_radius = 0.0
    glow_color_rgba = (1.0, 1.0, 1.0, 1.0)
    glow_intensity = 1.0
    if glow_config and isinstance(glow_config, dict):
        glow_radius = float(glow_config.get("radius", 0))
        glow_color_rgba = _parse_color(glow_config.get("color", "#FFFFFF"))
        glow_intensity = float(glow_config.get("intensity", 1.0))
    use_glow = glow_radius > 0

    # Load font. Prefer a real scalable TrueType face so ``font_size`` (in px)
    # actually changes glyph height; only fall back to the legacy fixed-size
    # ``load_default`` bitmap when no scalable face is resolvable at all.
    try:
        font, _font_source, _font_is_truetype = _resolve_font(
            font_path, font_size, font_weight
        )
    except Exception:
        font = ImageFont.load_default()

    def text_content_fn(_frame_index: int) -> np.ndarray:
        """Render text to canvas-sized RGBA."""
        # Split text into lines.
        lines = text.split("\n")

        # Measure each line to compute block dimensions.
        line_bboxes = []
        for line in lines:
            try:
                temp_img = PILImage.new("RGBA", (1, 1))
                temp_draw = ImageDraw.Draw(temp_img)
                bbox = temp_draw.textbbox((0, 0), line, font=font)
                line_bboxes.append(bbox)
            except Exception:
                # Fallback: estimate width/height.
                w = max(len(line) * font_size // 2, 10)
                h = font_size
                line_bboxes.append((0, 0, w, h))

        if not line_bboxes:
            return np.zeros((ctx.height, ctx.width, 4), dtype=np.float32)

        # Compute line width/height.
        line_widths = [bbox[2] - bbox[0] for bbox in line_bboxes]
        line_heights = [bbox[3] - bbox[1] for bbox in line_bboxes]

        # When tracking is active, the natural bbox width understates the line:
        # widen each line to the tracked advance so glyphs and alignment fit.
        if use_tracking:
            line_widths = [
                max(line_widths[i], int(round(_line_advance(font, line, letter_spacing))))
                for i, line in enumerate(lines)
            ]

        # Compute block dimensions.
        block_width = max(line_widths) if line_widths else font_size
        avg_line_height = sum(line_heights) / len(line_heights) if line_heights else font_size
        block_height = sum(line_heights) + (len(lines) - 1) * avg_line_height * (line_spacing - 1.0)

        # Add padding for stroke and effects (glow needs room outside glyphs).
        padding = int(stroke_width) + 2
        if use_glow:
            padding += int(glow_radius) * 2 + 2
        block_width = int(block_width + 2 * padding)
        block_height = int(block_height + 2 * padding)

        # Step 1: Create background layer if configured.
        if background_rgba:
            bg_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
            bg_draw = ImageDraw.Draw(bg_canvas)
            bg_fill = tuple(
                int(min(255, max(0, c * 255)))
                for c in background_rgba
            )
            bg_padding = 4
            bg_draw.rectangle(
                [bg_padding, bg_padding, block_width - bg_padding, block_height - bg_padding],
                fill=bg_fill
            )
            result_canvas = bg_canvas
        else:
            result_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))

        # Step 2: Draw shadow if configured (below text).
        if shadow_blur > 0 or shadow_offset != (0.0, 0.0):
            shadow_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_canvas)
            shadow_fill = tuple(
                int(min(255, max(0, c * 255)))
                for c in shadow_color_rgba[:3]
            ) + (255,)

            y_offset = float(padding)
            for i, line in enumerate(lines):
                if i > 0:
                    y_offset += line_heights[i - 1] * line_spacing

                if align == "left":
                    x_pos = float(padding)
                elif align == "right":
                    x_pos = block_width - line_widths[i] - padding
                else:  # center
                    x_pos = (block_width - line_widths[i]) / 2.0

                shadow_x = x_pos + shadow_offset[0]
                shadow_y = y_offset + shadow_offset[1]

                if use_tracking:
                    _draw_line_spaced(
                        shadow_draw, (shadow_x, shadow_y), line, font,
                        shadow_fill, letter_spacing,
                    )
                else:
                    shadow_draw.text(
                        (shadow_x, shadow_y), line,
                        fill=shadow_fill, font=font
                    )

            # Apply blur to shadow.
            if shadow_blur > 0:
                shadow_canvas = shadow_canvas.filter(
                    ImageFilter.GaussianBlur(radius=float(shadow_blur))
                )

            # Composite shadow under background/text.
            result_canvas.paste(shadow_canvas, (0, 0), shadow_canvas)

        # Step 2.5: Outer glow (drawn under the text, halo extends past glyphs).
        if use_glow:
            glow_canvas = PILImage.new(
                "RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0)
            )
            glow_draw = ImageDraw.Draw(glow_canvas)
            glow_fill = tuple(
                int(min(255, max(0, c * 255))) for c in glow_color_rgba[:3]
            ) + (255,)

            y_offset = float(padding)
            for i, line in enumerate(lines):
                if i > 0:
                    y_offset += line_heights[i - 1] * line_spacing
                if align == "left":
                    x_pos = float(padding)
                elif align == "right":
                    x_pos = block_width - line_widths[i] - padding
                else:
                    x_pos = (block_width - line_widths[i]) / 2.0

                if use_tracking:
                    _draw_line_spaced(
                        glow_draw, (x_pos, y_offset), line, font, glow_fill,
                        letter_spacing,
                    )
                else:
                    glow_draw.text((x_pos, y_offset), line, fill=glow_fill, font=font)

            # Blur to spread the halo outward.
            glow_canvas = glow_canvas.filter(
                ImageFilter.GaussianBlur(radius=float(glow_radius))
            )
            # Boost alpha by intensity (blur dilutes it; CapCut glow is punchy).
            if glow_intensity != 1.0:
                ga = np.asarray(glow_canvas, dtype=np.float32)
                ga[:, :, 3] = np.clip(ga[:, :, 3] * glow_intensity, 0, 255)
                glow_canvas = PILImage.fromarray(ga.astype(np.uint8), "RGBA")
            result_canvas.paste(glow_canvas, (0, 0), glow_canvas)

        # Step 3: Draw text with optional stroke.
        text_canvas = PILImage.new("RGBA", (int(block_width), int(block_height)), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_canvas)

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

        y_offset = float(padding)
        for i, line in enumerate(lines):
            if i > 0:
                y_offset += line_heights[i - 1] * line_spacing

            if align == "left":
                x_pos = float(padding)
            elif align == "right":
                x_pos = block_width - line_widths[i] - padding
            else:  # center
                x_pos = (block_width - line_widths[i]) / 2.0

            if use_tracking:
                _draw_line_spaced(
                    text_draw, (x_pos, y_offset), line, font, text_fill,
                    letter_spacing,
                    stroke_width=stroke_width if stroke_width > 0 else 0,
                    stroke_fill=stroke_fill,
                )
            else:
                text_draw.text(
                    (x_pos, y_offset), line,
                    fill=text_fill, font=font,
                    stroke_width=stroke_width if stroke_width > 0 else 0,
                    stroke_fill=stroke_fill
                )

        # Step 3.5: Apply a vertical gradient over the glyph fill (top -> bottom).
        # The glyph alpha is preserved as a mask; only RGB is replaced by the
        # gradient ramp, so antialiased edges and stroke alpha stay intact.
        if use_gradient:
            ta = np.asarray(text_canvas, dtype=np.float32) / 255.0
            h_px = ta.shape[0]
            ramp = np.linspace(0.0, 1.0, h_px, dtype=np.float32)[:, None]  # (H,1)
            gf = np.array(gradient_from_rgba[:3], dtype=np.float32)  # top
            gt = np.array(gradient_to_rgba[:3], dtype=np.float32)    # bottom
            grad_rgb = gf[None, None, :] * (1.0 - ramp[:, :, None]) + \
                gt[None, None, :] * ramp[:, :, None]  # (H,1,3)
            grad_rgb = np.broadcast_to(grad_rgb, (h_px, ta.shape[1], 3))
            alpha = ta[:, :, 3:4]
            # Only recolor pixels belonging to the core glyph fill (not stroke):
            # use the original glyph color as a selector so stroke keeps its hue.
            fill_sel = np.all(
                np.isclose(ta[:, :, :3], np.array(color_rgba[:3], np.float32),
                           atol=2.0 / 255.0),
                axis=2, keepdims=True,
            ).astype(np.float32)
            new_rgb = grad_rgb * fill_sel + ta[:, :, :3] * (1.0 - fill_sel)
            out = np.concatenate([new_rgb, alpha], axis=2)
            text_canvas = PILImage.fromarray(
                (np.clip(out, 0, 1) * 255.0).astype(np.uint8), "RGBA"
            )

        # Step 4: Composite text on top of shadow/background.
        result_canvas.paste(text_canvas, (0, 0), text_canvas)

        # Step 5: Centre the text block on the canvas.
        canvas = PILImage.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
        x_pos = (ctx.width - int(block_width)) // 2
        y_pos = (ctx.height - int(block_height)) // 2
        canvas.paste(result_canvas, (x_pos, y_pos), result_canvas)

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
