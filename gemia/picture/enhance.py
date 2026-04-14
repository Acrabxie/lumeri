"""gemia.picture.enhance — AI upscaling and color matching utilities."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}


def _is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _VIDEO_EXTS


# ---------------------------------------------------------------------------
# super_scale
# ---------------------------------------------------------------------------

def super_scale(input_path: str, output_path: str, *, factor: int = 2) -> str:
    """Upscale *input_path* by *factor* and write result to *output_path*.

    Uses Real-ESRGAN when available; falls back to ffmpeg lanczos filter.
    Works for both images (.jpg/.png) and videos (.mp4/.mov).

    Returns *output_path*.
    """
    if _is_video(input_path):
        _super_scale_video_ffmpeg(input_path, output_path, factor)
        return output_path

    # Try Real-ESRGAN for images
    try:
        _super_scale_image_realesrgan(input_path, output_path, factor)
    except ImportError:
        _super_scale_image_ffmpeg(input_path, output_path, factor)

    return output_path


def _super_scale_image_realesrgan(input_path: str, output_path: str, factor: int) -> None:
    import numpy as np
    from PIL import Image  # noqa: PLC0415
    from realesrgan import RealESRGANer  # noqa: PLC0415
    from basicsr.archs.rrdbnet_arch import RRDBNet  # noqa: PLC0415

    # Locate model weights — search common locations
    model_candidates = [
        os.path.join(os.path.dirname(__file__), "weights", "RealESRGAN_x4plus.pth"),
        os.path.expanduser("~/.cache/realesrgan/RealESRGAN_x4plus.pth"),
        "RealESRGAN_x4plus.pth",
    ]
    model_path = next((p for p in model_candidates if os.path.isfile(p)), model_candidates[0])

    model = RRDBNet(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        num_block=23, num_grow_ch=32, scale=4,
    )
    upsampler = RealESRGANer(
        scale=4,
        model_path=model_path,
        model=model,
        tile=0,
        tile_pad=10,
        pre_pad=0,
        half=False,
    )

    img = np.array(Image.open(input_path).convert("RGB"))
    # Real-ESRGAN always outputs 4× — resize again if a different factor was requested
    output, _ = upsampler.enhance(img, outscale=factor)
    Image.fromarray(output).save(output_path)


def _super_scale_image_ffmpeg(input_path: str, output_path: str, factor: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale=iw*{factor}:ih*{factor}:flags=lanczos",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _super_scale_video_ffmpeg(input_path: str, output_path: str, factor: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale=iw*{factor}:ih*{factor}:flags=lanczos",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# match_color
# ---------------------------------------------------------------------------

def match_color(source_path: str, reference_path: str, output_path: str) -> str:
    """Color-grade *source_path* to match the palette of *reference_path*.

    Uses per-channel histogram matching (CDF-based lookup table).
    For video sources the operation is applied frame-by-frame.

    Returns *output_path*.
    """
    if _is_video(source_path):
        _match_color_video(source_path, reference_path, output_path)
        return output_path

    _match_color_image(source_path, reference_path, output_path)
    return output_path


def _build_channel_lut(src_channel, ref_channel):
    """Return a 256-element uint8 LUT mapping source→reference distribution."""
    import numpy as np  # noqa: PLC0415

    def cdf(channel):
        hist, _ = np.histogram(channel.flatten(), bins=256, range=(0, 256))
        c = hist.cumsum().astype(np.float64)
        c /= c[-1]  # normalize to [0, 1]
        return c

    src_cdf = cdf(src_channel)
    ref_cdf = cdf(ref_channel)

    lut = np.zeros(256, dtype=np.uint8)
    ref_idx = 0
    for src_idx in range(256):
        while ref_idx < 255 and ref_cdf[ref_idx] < src_cdf[src_idx]:
            ref_idx += 1
        lut[src_idx] = ref_idx
    return lut


def _match_color_image(source_path: str, reference_path: str, output_path: str) -> None:
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    src = np.array(Image.open(source_path).convert("RGB"))
    ref = np.array(Image.open(reference_path).convert("RGB"))

    result = np.empty_like(src)
    for ch in range(3):
        lut = _build_channel_lut(src[:, :, ch], ref[:, :, ch])
        result[:, :, ch] = lut[src[:, :, ch]]

    Image.fromarray(result).save(output_path)


def _match_color_video(source_path: str, reference_path: str, output_path: str) -> None:
    from gemia.video.frames import apply_picture_op_to_video  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    # Pre-load reference image once; build LUTs outside the per-frame closure
    ref = np.array(Image.open(reference_path).convert("RGB"))

    def _frame_op(frame_bgr):
        """frame_bgr is a float32 [0,1] BGR ndarray as per gemia convention."""
        import cv2  # noqa: PLC0415

        src_uint8 = (frame_bgr[:, :, ::-1] * 255).clip(0, 255).astype(np.uint8)  # BGR→RGB
        result = np.empty_like(src_uint8)
        for ch in range(3):
            lut = _build_channel_lut(src_uint8[:, :, ch], ref[:, :, ch])
            result[:, :, ch] = lut[src_uint8[:, :, ch]]
        # Convert back to float32 BGR
        return (result[:, :, ::-1] / 255.0).astype(np.float32)

    apply_picture_op_to_video(source_path, output_path, op=_frame_op)


# ---------------------------------------------------------------------------
# skin_tone_protect
# ---------------------------------------------------------------------------

def skin_tone_protect(
    input_path: str,
    output_path: str,
    *,
    params: dict | None = None,
) -> str:
    """Protect skin tones during color grading by masking non-skin areas.

    params keys:
      hue_range        – (low, high) HSV hue degrees for skin detection (default (0, 35))
      saturation_boost – float, boost applied only to non-skin pixels (default 0.0)
      lightness_adjust – float -1.0..1.0, lightness delta for non-skin pixels (default 0.0)

    Returns output_path.
    """
    p = params or {}
    hue_range: tuple[float, float] = p.get("hue_range", (0, 35))
    saturation_boost: float = p.get("saturation_boost", 0.0)
    lightness_adjust: float = p.get("lightness_adjust", 0.0)

    if _is_video(input_path):
        from gemia.video.frames import apply_picture_op_to_video  # noqa: PLC0415

        def _frame_op(frame_bgr):
            return _skin_tone_protect_array(
                frame_bgr, hue_range, saturation_boost, lightness_adjust
            )

        apply_picture_op_to_video(input_path, output_path, op=_frame_op)
        return output_path

    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    img = np.array(Image.open(input_path).convert("RGB")).astype(np.float32) / 255.0
    # Convert to float32 BGR for _skin_tone_protect_array
    img_bgr = img[:, :, ::-1]
    result_bgr = _skin_tone_protect_array(img_bgr, hue_range, saturation_boost, lightness_adjust)
    result_rgb = (result_bgr[:, :, ::-1] * 255).clip(0, 255).astype(np.uint8)
    Image.fromarray(result_rgb).save(output_path)
    return output_path


def _skin_tone_protect_array(frame_bgr, hue_range, saturation_boost, lightness_adjust):
    """Apply skin-tone-aware adjustments to a float32 BGR [0,1] array."""
    import numpy as np  # noqa: PLC0415
    import colorsys

    h_lo, h_hi = hue_range[0] / 360.0, hue_range[1] / 360.0

    # Work in float32
    arr = frame_bgr.astype(np.float32)
    h, w = arr.shape[:2]

    # Convert RGB->HSV per-pixel via vectorised approach
    r = arr[:, :, 2]
    g = arr[:, :, 1]
    b = arr[:, :, 0]

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Hue
    hue = np.zeros((h, w), dtype=np.float32)
    mask_r = (cmax == r) & (delta > 0)
    mask_g = (cmax == g) & (delta > 0)
    mask_b = (cmax == b) & (delta > 0)
    hue[mask_r] = ((g[mask_r] - b[mask_r]) / delta[mask_r]) % 6
    hue[mask_g] = (b[mask_g] - r[mask_g]) / delta[mask_g] + 2
    hue[mask_b] = (r[mask_b] - g[mask_b]) / delta[mask_b] + 4
    hue /= 6.0  # normalise to [0, 1]

    # Saturation
    sat = np.where(cmax > 0, delta / cmax, 0.0).astype(np.float32)
    val = cmax

    # Skin mask: hue in range AND saturation > 0.1 AND value > 0.1
    skin_mask = (hue >= h_lo) & (hue <= h_hi) & (sat > 0.1) & (val > 0.1)
    non_skin = ~skin_mask

    result = arr.copy()

    # Apply saturation boost to non-skin pixels
    if saturation_boost != 0.0:
        boost = np.clip(sat + saturation_boost, 0.0, 1.0) - sat  # actual delta
        # Resaturate: move each channel toward/away from value
        for ch_idx in range(3):
            ch = result[:, :, ch_idx]
            delta_ch = val - ch
            result[:, :, ch_idx] = np.where(
                non_skin,
                np.clip(ch - boost * delta_ch, 0.0, 1.0),
                ch,
            )

    # Apply lightness adjust to non-skin pixels
    if lightness_adjust != 0.0:
        adj = lightness_adjust
        for ch_idx in range(3):
            ch = result[:, :, ch_idx]
            result[:, :, ch_idx] = np.where(
                non_skin,
                np.clip(ch + adj, 0.0, 1.0),
                ch,
            )

    # Blend back: skin pixels restored from original
    for ch_idx in range(3):
        result[:, :, ch_idx] = np.where(skin_mask, arr[:, :, ch_idx], result[:, :, ch_idx])

    return result


# ---------------------------------------------------------------------------
# hdr_grade
# ---------------------------------------------------------------------------

def hdr_grade(
    input_path: str,
    output_path: str,
    *,
    target_format: str = "hlg",
) -> str:
    """Grade video to HDR format using ffmpeg zscale filter.

    target_format: "hlg" (Hybrid Log-Gamma) or "pq" (HDR10 / SMPTE ST 2084).

    Returns output_path.
    """
    if target_format == "pq":
        vf = (
            "zscale=transfer=smpte2084:matrix=bt2020nc:primaries=bt2020:range=limited,"
            "format=yuv420p10le"
        )
        color_args = [
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
            "-color_primaries", "bt2020",
        ]
    else:  # default hlg
        vf = "zscale=transfer=hlg:matrix=bt2020nc:primaries=bt2020,format=yuv420p10le"
        color_args = [
            "-color_trc", "hlg",
            "-colorspace", "bt2020nc",
            "-color_primaries", "bt2020",
        ]

    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            *color_args,
            "-c:a", "copy",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # zscale unavailable — approximate HDR look with curves + saturation boost
        from PIL import Image
        import numpy as np
        img = np.array(Image.open(input_path).convert("RGB")).astype(np.float32) / 255.0
        # OOTF-style gamma expand and contrast boost
        img = np.clip(img ** 0.9 * 1.1, 0, 1)
        # Slight warm shift for HLG look
        img[:, :, 0] = np.clip(img[:, :, 0] * 1.04, 0, 1)
        img[:, :, 2] = np.clip(img[:, :, 2] * 0.97, 0, 1)
        Image.fromarray((img * 255).astype(np.uint8)).save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# film_grain_organic
# ---------------------------------------------------------------------------

def film_grain_organic(
    input_path: str,
    output_path: str,
    *,
    params: dict | None = None,
) -> str:
    """Add organic film grain texture using ffmpeg noise filter.

    params keys:
      strength – float 0.0-1.0, grain intensity (default 0.3)
      size     – int 1-5, grain size (default 2, reserved for future use)
      colored  – bool, use colored grain instead of luma-only (default False)

    Returns output_path.
    """
    p = params or {}
    strength: float = float(p.get("strength", 0.3))
    size: int = int(p.get("size", 2))
    colored: bool = bool(p.get("colored", False))

    s = int(strength * 100)

    if colored:
        noise_filter = (
            f"noise=c0s={s}:c0f=t:c1s={s // 2}:c1f=t:c2s={s // 2}:c2f=t"
        )
    else:
        noise_filter = f"noise=alls={s}:allf=t"

    vf = f"unsharp=3:3:0.5,{noise_filter}"

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


# ---------------------------------------------------------------------------
# defocus_background
# ---------------------------------------------------------------------------
def defocus_background(
    input_path: str,
    output_path: str,
    *,
    depth_map: str | None = None,
    blur_strength: int = 15,
) -> str:
    """Apply depth-of-field blur, keeping foreground sharp.

    Args:
        input_path: Source image or video.
        output_path: Destination.
        depth_map: Optional path to grayscale depth map (bright=near, dark=far).
                   If None a radial gradient (sharp centre) is used.
        blur_strength: Blur radius in pixels for background regions.

    Returns:
        output_path
    """
    if _is_video(input_path):
        bs = blur_strength | 1  # must be odd for boxblur
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"boxblur={bs}:{bs}",
            "-c:a", "copy",
            output_path,
        ], check=True, capture_output=True)
        return output_path

    from PIL import Image, ImageFilter
    import numpy as np

    src = Image.open(input_path).convert("RGB")
    w, h = src.size
    blurred = src.filter(ImageFilter.GaussianBlur(radius=blur_strength))

    if depth_map:
        mask = Image.open(depth_map).convert("L").resize((w, h))
    else:
        # Radial gradient: white at centre → black at edges
        cx, cy = w / 2, h / 2
        ys, xs = np.ogrid[:h, :w]
        dist = np.sqrt(((xs - cx) / cx) ** 2 + ((ys - cy) / cy) ** 2)
        dist = np.clip(1.0 - dist, 0.0, 1.0)
        mask = Image.fromarray((dist * 255).astype(np.uint8))

    result = Image.composite(src, blurred, mask)
    result.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# relight
# ---------------------------------------------------------------------------
def relight(
    input_path: str,
    output_path: str,
    *,
    light_direction: str = "top",
) -> str:
    """Simulate directional relighting via gradient multiply.

    Args:
        input_path: Source image or video.
        output_path: Destination.
        light_direction: One of ``"top"``, ``"bottom"``, ``"left"``, ``"right"``,
                         ``"top-left"``, ``"top-right"``.

    Returns:
        output_path
    """
    def _apply(img):
        import numpy as np
        from PIL import Image as _Image
        arr = np.array(img).astype(np.float32) / 255.0
        h, w = arr.shape[:2]
        xs = np.linspace(0.4, 1.2, w)
        ys = np.linspace(0.4, 1.2, h)
        x_grad = xs[np.newaxis, :]
        y_grad = ys[:, np.newaxis]

        d = light_direction.lower()
        if d == "top":
            grad = np.linspace(1.2, 0.4, h)[:, np.newaxis] * np.ones((1, w))
        elif d == "bottom":
            grad = np.linspace(0.4, 1.2, h)[:, np.newaxis] * np.ones((1, w))
        elif d == "left":
            grad = np.ones((h, 1)) * np.linspace(1.2, 0.4, w)[np.newaxis, :]
        elif d == "right":
            grad = np.ones((h, 1)) * np.linspace(0.4, 1.2, w)[np.newaxis, :]
        elif d == "top-left":
            grad = (np.linspace(1.2, 0.4, h)[:, np.newaxis] + np.linspace(1.2, 0.4, w)[np.newaxis, :]) / 2
        elif d == "top-right":
            grad = (np.linspace(1.2, 0.4, h)[:, np.newaxis] + np.linspace(0.4, 1.2, w)[np.newaxis, :]) / 2
        else:
            raise ValueError(f"Unknown light_direction '{light_direction}'. "
                             f"Choose from: top, bottom, left, right, top-left, top-right")

        grad = grad[:, :, np.newaxis]
        result = np.clip(arr * grad, 0.0, 1.0)
        return _Image.fromarray((result * 255).astype(np.uint8))

    if _is_video(input_path):
        from gemia.video.frames import apply_picture_op_to_video
        apply_picture_op_to_video(input_path, output_path, op=_apply)
        return output_path

    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    _apply(img).save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# motion_blur
# ---------------------------------------------------------------------------
def motion_blur(
    input_path: str,
    output_path: str,
    *,
    vector_field: dict | None = None,
) -> str:
    """Apply directional motion blur.

    Args:
        input_path: Source image or video.
        output_path: Destination.
        vector_field: Dict with ``angle`` (degrees, 0=horizontal) and
                      ``strength`` (kernel size in pixels, default 20).

    Returns:
        output_path
    """
    vf = vector_field or {}
    angle_deg = float(vf.get("angle", 0))
    strength = max(1, int(vf.get("strength", 20)))

    def _apply(img):
        import numpy as np
        from PIL import Image as _Image
        import math

        arr = np.array(img).astype(np.float32)
        k = strength if strength % 2 == 1 else strength + 1
        kernel = np.zeros((k, k), dtype=np.float32)
        cx = cy = k // 2
        angle_rad = math.radians(angle_deg)
        dx = math.cos(angle_rad)
        dy = math.sin(angle_rad)
        for i in range(-(k // 2), k // 2 + 1):
            px = int(round(cx + i * dx))
            py = int(round(cy + i * dy))
            if 0 <= px < k and 0 <= py < k:
                kernel[py, px] = 1.0
        s = kernel.sum()
        if s > 0:
            kernel /= s

        try:
            from scipy.ndimage import convolve
            result = np.stack([convolve(arr[:, :, c], kernel) for c in range(arr.shape[2])], axis=2)
            result = np.clip(result, 0, 255).astype(np.uint8)
            return _Image.fromarray(result)
        except ImportError:
            from PIL import ImageFilter
            return img.filter(ImageFilter.BLUR)

    if _is_video(input_path):
        from gemia.video.frames import apply_picture_op_to_video
        apply_picture_op_to_video(input_path, output_path, op=_apply)
        return output_path

    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    _apply(img).save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# color_balance
# ---------------------------------------------------------------------------

def color_balance(
    input_path: str,
    output_path: str,
    *,
    shadows: tuple[float, float, float] = (0.0, 0.0, 0.0),
    midtones: tuple[float, float, float] = (0.0, 0.0, 0.0),
    highlights: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> str:
    """Adjust color balance in shadow/midtone/highlight tonal ranges.

    Each range receives an RGB shift tuple where values are in [-1.0, 1.0].
    Positive values shift toward that channel; negative away.

    Args:
        input_path: Source image or video file.
        output_path: Destination file.
        shadows: (r, g, b) shift for dark pixels.
        midtones: (r, g, b) shift for mid-range pixels.
        highlights: (r, g, b) shift for bright pixels.

    Returns:
        The *output_path*.
    """
    import numpy as np
    from PIL import Image

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    img = np.array(Image.open(input_path).convert("RGB")).astype(np.float32) / 255.0

    # Build per-pixel luminance mask
    lum = img.mean(axis=2, keepdims=True)  # (H, W, 1)

    # Shadow weight: peaks at lum=0, zero at lum=0.5+
    w_shadow = np.clip(1.0 - lum * 2.0, 0, 1)
    # Highlight weight: peaks at lum=1, zero at lum=0.5-
    w_highlight = np.clip((lum - 0.5) * 2.0, 0, 1)
    # Midtone weight: peaks at lum=0.5, zero at extremes
    w_mid = np.clip(1.0 - np.abs(lum - 0.5) * 4.0, 0, 1)

    sr, sg, sb = [float(x) * 0.3 for x in shadows]
    mr, mg, mb = [float(x) * 0.3 for x in midtones]
    hr, hg, hb = [float(x) * 0.3 for x in highlights]

    img[:, :, 0] += w_shadow[:, :, 0] * sr + w_mid[:, :, 0] * mr + w_highlight[:, :, 0] * hr
    img[:, :, 1] += w_shadow[:, :, 0] * sg + w_mid[:, :, 0] * mg + w_highlight[:, :, 0] * hg
    img[:, :, 2] += w_shadow[:, :, 0] * sb + w_mid[:, :, 0] * mb + w_highlight[:, :, 0] * hb

    img = np.clip(img, 0, 1)
    Image.fromarray((img * 255).astype(np.uint8)).save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# color_lookup
# ---------------------------------------------------------------------------

def color_lookup(
    input_path: str,
    output_path: str,
    *,
    lut_file: str,
    strength: float = 1.0,
) -> str:
    """Apply a .cube LUT file to an image using trilinear interpolation.

    Args:
        input_path: Source image file.
        output_path: Destination image file.
        lut_file: Path to a ``.cube`` LUT file (3D LUT, 17³ or 33³ grid).
        strength: Blend strength between original (0.0) and LUT-graded (1.0).

    Returns:
        The *output_path*.
    """
    import numpy as np
    from PIL import Image

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Parse .cube file
    size = 0
    table: list[list[float]] = []
    with open(lut_file, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith("LUT_3D_SIZE"):
                size = int(line.split()[-1])
            elif line and not line.startswith("#") and not line.startswith("TITLE") \
                    and not line.startswith("DOMAIN") and not line.startswith("LUT"):
                parts = line.split()
                if len(parts) == 3:
                    try:
                        table.append([float(x) for x in parts])
                    except ValueError:
                        pass

    if size == 0 or not table:
        raise ValueError(f"Could not parse LUT from {lut_file!r}")

    lut_data = np.array(table, dtype=np.float32).reshape(size, size, size, 3)

    img = np.array(Image.open(input_path).convert("RGB")).astype(np.float32) / 255.0

    # Trilinear lookup
    def _trilinear(img_arr: np.ndarray, lut: np.ndarray, n: int) -> np.ndarray:
        idx = img_arr * (n - 1)
        i0 = np.floor(idx).astype(int).clip(0, n - 2)
        i1 = i0 + 1
        f = idx - i0  # fractional part

        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

        # Trilinear interpolation over 8 corners
        c000 = lut[r0, g0, b0]
        c100 = lut[r1, g0, b0]
        c010 = lut[r0, g1, b0]
        c110 = lut[r1, g1, b0]
        c001 = lut[r0, g0, b1]
        c101 = lut[r1, g0, b1]
        c011 = lut[r0, g1, b1]
        c111 = lut[r1, g1, b1]

        return (
            c000 * (1-fr)*(1-fg)*(1-fb) +
            c100 * fr*(1-fg)*(1-fb) +
            c010 * (1-fr)*fg*(1-fb) +
            c110 * fr*fg*(1-fb) +
            c001 * (1-fr)*(1-fg)*fb +
            c101 * fr*(1-fg)*fb +
            c011 * (1-fr)*fg*fb +
            c111 * fr*fg*fb
        )

    graded = _trilinear(img, lut_data, size)
    result = img * (1.0 - strength) + graded * strength
    result = np.clip(result, 0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# batch_image_resize
# ---------------------------------------------------------------------------

def batch_image_resize(
    input_dir: str,
    output_dir: str,
    *,
    width: int,
    height: int,
    fit: str = "contain",
    bg_color: tuple[int, int, int] = (0, 0, 0),
    exts: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"),
) -> list[str]:
    """Resize all images in a directory to the target dimensions.

    Args:
        input_dir: Source directory containing image files.
        output_dir: Destination directory for resized images.
        width: Target width in pixels.
        height: Target height in pixels.
        fit: ``"contain"`` (letterbox/pillarbox, preserves aspect),
            ``"cover"`` (crop to fill), or ``"stretch"`` (ignore aspect).
        bg_color: Background fill colour for ``"contain"`` mode (RGB).
        exts: File extensions to process (case-insensitive).

    Returns:
        List of output file paths.
    """
    import glob, numpy as np
    from pathlib import Path as _Path
    from PIL import Image

    _Path(output_dir).mkdir(parents=True, exist_ok=True)

    files = [
        f for f in sorted(_Path(input_dir).iterdir())
        if f.suffix.lower() in exts
    ]
    if not files:
        return []

    outputs: list[str] = []
    for src in files:
        img = Image.open(src).convert("RGB")
        iw, ih = img.size

        if fit == "stretch":
            resized = img.resize((width, height), Image.LANCZOS)
        elif fit == "cover":
            scale = max(width / iw, height / ih)
            nw, nh = int(iw * scale), int(ih * scale)
            resized = img.resize((nw, nh), Image.LANCZOS)
            left = (nw - width) // 2
            top = (nh - height) // 2
            resized = resized.crop((left, top, left + width, top + height))
        else:  # contain
            scale = min(width / iw, height / ih)
            nw, nh = int(iw * scale), int(ih * scale)
            resized_inner = img.resize((nw, nh), Image.LANCZOS)
            canvas = Image.new("RGB", (width, height), bg_color)
            x_off = (width - nw) // 2
            y_off = (height - nh) // 2
            canvas.paste(resized_inner, (x_off, y_off))
            resized = canvas

        out_path = str(_Path(output_dir) / src.name)
        resized.save(out_path)
        outputs.append(out_path)

    return outputs


# ---------------------------------------------------------------------------
# image_collage
# ---------------------------------------------------------------------------

def image_collage(
    image_paths: list[str],
    output_path: str,
    *,
    cols: int = 2,
    cell_width: int = 400,
    cell_height: int = 300,
    padding: int = 10,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> str:
    """Assemble multiple images into a grid collage.

    Args:
        image_paths: List of source image file paths.
        output_path: Destination image file.
        cols: Number of columns in the grid.
        cell_width: Width of each cell in pixels.
        cell_height: Height of each cell in pixels.
        padding: Gap between cells and border in pixels.
        bg_color: Background fill colour (RGB).

    Returns:
        The *output_path*.
    """
    import math
    import numpy as np
    from PIL import Image

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    n = len(image_paths)
    if n == 0:
        raise ValueError("image_collage: no images provided")

    rows = math.ceil(n / cols)
    total_w = cols * cell_width + (cols + 1) * padding
    total_h = rows * cell_height + (rows + 1) * padding

    canvas = Image.new("RGB", (total_w, total_h), bg_color)

    for idx, path in enumerate(image_paths):
        row = idx // cols
        col = idx % cols
        img = Image.open(path).convert("RGB")

        # Fit image into cell with letterboxing
        iw, ih = img.size
        scale = min(cell_width / iw, cell_height / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img_resized = img.resize((nw, nh), Image.LANCZOS)

        x = padding + col * (cell_width + padding) + (cell_width - nw) // 2
        y = padding + row * (cell_height + padding) + (cell_height - nh) // 2
        canvas.paste(img_resized, (x, y))

    canvas.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# image_sharpen
# ---------------------------------------------------------------------------

def image_sharpen(
    input_path: str,
    output_path: str,
    *,
    strength: float = 1.0,
    radius: int = 2,
    threshold: int = 3,
) -> str:
    """Sharpen an image using PIL unsharp mask.

    Args:
        input_path: Source image file.
        output_path: Destination image file.
        strength: Sharpening amount multiplier (1.0 = standard unsharp mask).
        radius: Blur radius for unsharp mask in pixels.
        threshold: Minimum brightness difference to sharpen (0–255).

    Returns:
        The *output_path*.
    """
    from PIL import Image, ImageFilter

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    img = Image.open(input_path).convert("RGB")
    # PIL UnsharpMask: radius, percent (strength*100), threshold
    sharpened = img.filter(
        ImageFilter.UnsharpMask(radius=radius, percent=int(strength * 150), threshold=threshold)
    )
    sharpened.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# image_blur
# ---------------------------------------------------------------------------

def image_blur(
    input_path: str,
    output_path: str,
    *,
    radius: float = 2.0,
) -> str:
    """Apply Gaussian blur to an image.

    Args:
        input_path: Source image file.
        output_path: Destination image file.
        radius: Blur radius in pixels (larger = more blur).

    Returns:
        The *output_path*.
    """
    from PIL import Image, ImageFilter

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    img = Image.open(input_path).convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    blurred.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# image_contrast
# ---------------------------------------------------------------------------

def image_contrast(
    input_path: str,
    output_path: str,
    *,
    factor: float = 1.5,
) -> str:
    """Adjust image contrast.

    Args:
        input_path: Source image file.
        output_path: Destination image file.
        factor: Contrast multiplier. 1.0 = original, > 1.0 = more contrast,
            0.0 = solid grey.

    Returns:
        The *output_path*.
    """
    from PIL import Image, ImageEnhance

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img = Image.open(input_path).convert("RGB")
    enhanced = ImageEnhance.Contrast(img).enhance(factor)
    enhanced.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# image_saturation
# ---------------------------------------------------------------------------

def image_saturation(
    input_path: str,
    output_path: str,
    *,
    factor: float = 1.5,
) -> str:
    """Adjust image color saturation.

    Args:
        input_path: Source image file.
        output_path: Destination image file.
        factor: Saturation multiplier. 1.0 = original, > 1.0 = more vivid,
            0.0 = grayscale.

    Returns:
        The *output_path*.
    """
    from PIL import Image, ImageEnhance

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img = Image.open(input_path).convert("RGB")
    enhanced = ImageEnhance.Color(img).enhance(factor)
    enhanced.save(output_path)
    return output_path


def image_flip(input_path: str, output_path: str, *, direction: str = "horizontal") -> None:
    """Flip image horizontally or vertically using PIL.
    
    Args:
        direction: 'horizontal' or 'vertical'
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    if direction == "horizontal":
        flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
    elif direction == "vertical":
        flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
    else:
        raise ValueError(f"direction must be 'horizontal' or 'vertical', got {direction!r}")
    flipped.save(output_path)


def image_rotate(input_path: str, output_path: str, *, angle: float = 90.0, expand: bool = True) -> None:
    """Rotate image by arbitrary degrees.
    
    Args:
        angle: Rotation angle in degrees, counter-clockwise. Default 90.
        expand: If True, expand output to fit the rotated image. Default True.
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    rotated = img.rotate(angle, expand=expand)
    rotated.save(output_path)


def image_crop(input_path: str, output_path: str, *, left: int = 0, top: int = 0, right: int | None = None, bottom: int | None = None) -> None:
    """Crop image to a specific region.
    
    Args:
        left: Left pixel offset. Default 0.
        top: Top pixel offset. Default 0.
        right: Right pixel boundary. None means image width.
        bottom: Bottom pixel boundary. None means image height.
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    w, h = img.size
    r = right if right is not None else w
    b = bottom if bottom is not None else h
    cropped = img.crop((left, top, r, b))
    cropped.save(output_path)


def image_resize_to_fit(
    input_path: str,
    output_path: str,
    *,
    max_width: int = 1920,
    max_height: int = 1080,
) -> None:
    """Resize image to fit within max_width x max_height preserving aspect ratio.

    Args:
        max_width: Maximum output width in pixels.
        max_height: Maximum output height in pixels.
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    img.save(output_path)


def image_add_border(
    input_path: str,
    output_path: str,
    *,
    size: int = 20,
    color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Add a solid color border around an image.

    Args:
        size: Border width in pixels. Default 20.
        color: Border RGB color tuple. Default white (255, 255, 255).
    """
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    bordered = ImageOps.expand(img, border=size, fill=color)
    bordered.save(output_path)


def image_grayscale(input_path: str, output_path: str) -> None:
    """Convert image to grayscale."""
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    gray = ImageOps.grayscale(img).convert("RGB")
    gray.save(output_path)


def image_invert(input_path: str, output_path: str) -> None:
    """Invert image colors."""
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    inverted = ImageOps.invert(img)
    inverted.save(output_path)


def image_posterize(input_path: str, output_path: str, *, bits: int = 4) -> None:
    """Posterize image by reducing color bit depth.

    Args:
        bits: Number of bits per channel (1-8). Lower = more posterized. Default 4.
    """
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    posterized = ImageOps.posterize(img, bits)
    posterized.save(output_path)


def image_solarize(input_path: str, output_path: str, *, threshold: int = 128) -> None:
    """Apply solarize effect (invert pixels above threshold).

    Args:
        threshold: Pixel value threshold 0-255. Pixels above are inverted. Default 128.
    """
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    solarized = ImageOps.solarize(img, threshold=threshold)
    solarized.save(output_path)


def image_pixelate(input_path: str, output_path: str, *, block_size: int = 16) -> None:
    """Pixelate image by downscaling then upscaling with nearest-neighbor interpolation.

    Args:
        block_size: Pixel block size in pixels. Larger = more pixelated. Default 16.
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    w, h = img.size
    small_w = max(1, w // block_size)
    small_h = max(1, h // block_size)
    pixelated = img.resize((small_w, small_h), Image.NEAREST).resize((w, h), Image.NEAREST)
    pixelated.save(output_path)


def image_emboss(input_path: str, output_path: str) -> None:
    """Apply emboss effect to image."""
    from PIL import Image, ImageFilter
    img = Image.open(input_path).convert("RGB")
    embossed = img.filter(ImageFilter.EMBOSS)
    embossed.save(output_path)


def image_find_edges(input_path: str, output_path: str) -> None:
    """Apply edge detection to image using PIL FIND_EDGES filter."""
    from PIL import Image, ImageFilter
    img = Image.open(input_path).convert("RGB")
    edges = img.filter(ImageFilter.FIND_EDGES)
    edges.save(output_path)


def image_smooth(input_path: str, output_path: str) -> None:
    """Smooth image using PIL SMOOTH_MORE filter."""
    from PIL import Image, ImageFilter
    img = Image.open(input_path).convert("RGB")
    smoothed = img.filter(ImageFilter.SMOOTH_MORE)
    smoothed.save(output_path)


def image_auto_enhance(input_path: str, output_path: str) -> None:
    """Auto-enhance image by applying auto-contrast and equalizing histogram."""
    from PIL import Image, ImageOps
    img = Image.open(input_path).convert("RGB")
    enhanced = ImageOps.autocontrast(img, cutoff=1)
    enhanced.save(output_path)


def image_tint(
    input_path: str,
    output_path: str,
    *,
    color: tuple[int, int, int] = (255, 100, 0),
    strength: float = 0.3,
) -> None:
    """Apply a color tint to image by blending with a solid color.

    Args:
        color: RGB color tuple for the tint. Default orange (255, 100, 0).
        strength: Blend strength 0-1. 0 = no tint, 1 = full color. Default 0.3.
    """
    from PIL import Image
    import numpy as np
    img = np.array(Image.open(input_path).convert("RGB"), dtype=np.float32)
    tint = np.array(color, dtype=np.float32)
    blended = img * (1.0 - strength) + tint * strength
    blended = np.clip(blended, 0, 255).astype(np.uint8)
    Image.fromarray(blended).save(output_path)


def image_watermark_text(
    input_path: str,
    output_path: str,
    *,
    text: str = "WATERMARK",
    position: str = "bottom_right",
    opacity: float = 0.5,
    font_size: int = 24,
    color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Add text watermark to image.

    Args:
        text: Watermark text. Default 'WATERMARK'.
        position: One of 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center'. Default 'bottom_right'.
        opacity: Text opacity 0-1. Default 0.5.
        font_size: Font size in pixels. Default 24.
        color: RGB text color. Default white.
    """
    from PIL import Image, ImageDraw, ImageFont
    import os
    img = Image.open(input_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Try to use a default font
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = img.size
    margin = 10
    pos_map = {
        "top_left": (margin, margin),
        "top_right": (w - tw - margin, margin),
        "bottom_left": (margin, h - th - margin),
        "bottom_right": (w - tw - margin, h - th - margin),
        "center": ((w - tw) // 2, (h - th) // 2),
    }
    xy = pos_map.get(position, pos_map["bottom_right"])
    alpha = int(opacity * 255)
    draw.text(xy, text, font=font, fill=(*color, alpha))
    result = Image.alpha_composite(img, overlay).convert("RGB")
    result.save(output_path)


def image_rounded_corners(input_path: str, output_path: str, *, radius: int = 30) -> None:
    """Apply rounded corners to image using an alpha mask.

    Args:
        radius: Corner radius in pixels. Default 30.
    """
    from PIL import Image, ImageDraw
    img = Image.open(input_path).convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    img.putalpha(mask)
    # Save with transparency or convert to RGB on white background if output is JPEG
    if output_path.lower().endswith((".jpg", ".jpeg")):
        bg = Image.new("RGB", (w, h), (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        bg.save(output_path)
    else:
        img.save(output_path)


def image_composite_alpha(
    background_path: str,
    foreground_path: str,
    output_path: str,
    *,
    x: int = 0,
    y: int = 0,
) -> None:
    """Composite a foreground RGBA image over a background image.

    Args:
        background_path: Path to background image (any mode).
        foreground_path: Path to foreground image (should have alpha channel).
        x: X offset for foreground placement. Default 0.
        y: Y offset for foreground placement. Default 0.
    """
    from PIL import Image
    bg = Image.open(background_path).convert("RGBA")
    fg = Image.open(foreground_path).convert("RGBA")
    canvas = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    canvas.paste(bg, (0, 0))
    canvas.paste(fg, (x, y), mask=fg.split()[3])
    result = canvas.convert("RGB")
    result.save(output_path)


def image_adjust_hsl(
    input_path: str,
    output_path: str,
    *,
    hue_shift: float = 0.0,
    saturation: float = 1.0,
    lightness: float = 0.0,
) -> None:
    """Adjust image hue, saturation, and lightness.

    Args:
        hue_shift: Hue rotation in degrees (-180 to 180). Default 0.0.
        saturation: Saturation multiplier (0=gray, 1=original, 2=doubled). Default 1.0.
        lightness: Lightness offset (-1 to 1). Default 0.0.
    """
    import colorsys
    import numpy as np
    from PIL import Image
    img = np.array(Image.open(input_path).convert("RGB"), dtype=np.float32) / 255.0
    out = np.zeros_like(img)
    h_shift = hue_shift / 360.0
    for i in range(img.shape[0]):
        for j in range(img.shape[1]):
            r, g, b = img[i, j]
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            h = (h + h_shift) % 1.0
            s = max(0.0, min(1.0, s * saturation))
            l = max(0.0, min(1.0, l + lightness))
            out[i, j] = colorsys.hls_to_rgb(h, l, s)
    result = (np.clip(out, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(result).save(output_path)


def image_resize_canvas(
    input_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fill_color: tuple[int, int, int] = (0, 0, 0),
    anchor: str = "center",
) -> None:
    """Resize canvas to target dimensions, padding with fill_color without scaling the image.

    Args:
        width: Target canvas width in pixels.
        height: Target canvas height in pixels.
        fill_color: Background fill color RGB. Default black.
        anchor: Image placement: 'center', 'top_left', 'top_right', 'bottom_left', 'bottom_right'. Default 'center'.
    """
    from PIL import Image
    img = Image.open(input_path).convert("RGB")
    iw, ih = img.size
    canvas = Image.new("RGB", (width, height), fill_color)
    if anchor == "center":
        x, y = (width - iw) // 2, (height - ih) // 2
    elif anchor == "top_left":
        x, y = 0, 0
    elif anchor == "top_right":
        x, y = width - iw, 0
    elif anchor == "bottom_left":
        x, y = 0, height - ih
    elif anchor == "bottom_right":
        x, y = width - iw, height - ih
    else:
        x, y = (width - iw) // 2, (height - ih) // 2
    canvas.paste(img, (max(0, x), max(0, y)))
    canvas.save(output_path)


def image_collage(
    image_paths: list[str],
    output_path: str,
    *,
    cols: int = 3,
    thumb_width: int = 200,
    thumb_height: int = 150,
    gap: int = 5,
    bg_color: tuple[int, int, int] = (40, 40, 40),
) -> None:
    """Create a grid collage from multiple images.

    Args:
        image_paths: List of input image paths.
        cols: Number of columns in the grid. Default 3.
        thumb_width: Width of each thumbnail. Default 200.
        thumb_height: Height of each thumbnail. Default 150.
        gap: Gap in pixels between thumbnails. Default 5.
        bg_color: Background color RGB. Default dark gray.
    """
    from PIL import Image
    n = len(image_paths)
    rows = (n + cols - 1) // cols
    canvas_w = cols * thumb_width + (cols + 1) * gap
    canvas_h = rows * thumb_height + (rows + 1) * gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    for idx, path in enumerate(image_paths):
        img = Image.open(path).convert("RGB")
        img.thumbnail((thumb_width, thumb_height), Image.LANCZOS)
        col = idx % cols
        row = idx // cols
        x = gap + col * (thumb_width + gap) + (thumb_width - img.width) // 2
        y = gap + row * (thumb_height + gap) + (thumb_height - img.height) // 2
        canvas.paste(img, (x, y))
    canvas.save(output_path)


def image_sketch(
    input_path: str,
    output_path: str,
    *,
    blur_radius: int = 21,
    intensity: float = 1.0,
) -> None:
    """Convert an image to a pencil-sketch look.

    Args:
        blur_radius: Gaussian blur radius (odd int) for the dodge step. Default 21.
        intensity: Blend intensity 0–1. Default 1.0 (full sketch).
    """
    from PIL import Image, ImageFilter, ImageChops
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    gray = img.convert("L")
    # Invert and blur (dodge layer)
    inv = ImageChops.invert(gray)
    radius = max(1, blur_radius | 1)  # ensure odd
    blurred = inv.filter(ImageFilter.GaussianBlur(radius=radius))
    # Color dodge: gray / (1 - blurred/255)
    g = np.array(gray, dtype=np.float32)
    b = np.array(blurred, dtype=np.float32)
    dodge = np.clip(g / (1.0 - b / 255.0 + 1e-7) * 255.0, 0, 255).astype(np.uint8)
    sketch = Image.fromarray(dodge, "L")
    # Blend with original gray according to intensity
    if intensity < 1.0:
        sketch = Image.blend(gray, sketch, intensity)
    sketch.convert("RGB").save(output_path)


def image_oil_paint(
    input_path: str,
    output_path: str,
    *,
    radius: int = 4,
    levels: int = 8,
) -> None:
    """Apply an oil-paint stylization effect.

    Args:
        radius: Brush radius in pixels. Default 4.
        levels: Number of intensity levels for quantization. Default 8.
    """
    from PIL import Image
    import numpy as np
    from collections import Counter

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.uint8)
    h, w = arr.shape[:2]
    out = arr.copy()
    # Quantize intensity to discrete levels
    gray = (arr[..., 0].astype(np.float32) * 0.299
            + arr[..., 1].astype(np.float32) * 0.587
            + arr[..., 2].astype(np.float32) * 0.114)
    quantized = (gray / 255.0 * (levels - 1)).astype(np.int32)

    r = radius
    for y in range(r, h - r):
        for x in range(r, w - r):
            patch_q = quantized[y - r:y + r + 1, x - r:x + r + 1].ravel()
            patch_c = arr[y - r:y + r + 1, x - r:x + r + 1].reshape(-1, 3)
            # Find most common intensity level
            most_common_level = Counter(patch_q).most_common(1)[0][0]
            mask = patch_q == most_common_level
            out[y, x] = patch_c[mask].mean(axis=0).astype(np.uint8)

    Image.fromarray(out).save(output_path)


def image_cartoon(
    input_path: str,
    output_path: str,
    *,
    blur_radius: int = 5,
    edge_threshold: int = 100,
    levels: int = 6,
) -> None:
    """Apply a cartoon effect: color quantization + edge overlay.

    Args:
        blur_radius: Bilateral-like blur radius. Default 5.
        edge_threshold: Canny-style edge threshold (0-255). Default 100.
        levels: Color quantization levels. Default 6.
    """
    from PIL import Image, ImageFilter
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32)

    # Quantize colors
    quantized = (np.floor(arr / (256.0 / levels)) * (256.0 / levels)).clip(0, 255).astype(np.uint8)

    # Smooth the quantized image
    smooth = Image.fromarray(quantized).filter(ImageFilter.MedianFilter(size=max(3, blur_radius | 1)))

    # Edge detection on grayscale
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges_arr = np.array(edges)
    edge_mask = (edges_arr > edge_threshold).astype(np.uint8) * 255
    edge_img = Image.fromarray(edge_mask, "L")

    # Overlay edges as black lines onto smooth image
    smooth_arr = np.array(smooth)
    mask = np.array(edge_img) > 128
    smooth_arr[mask] = 0
    Image.fromarray(smooth_arr).save(output_path)


def image_sepia(
    input_path: str,
    output_path: str,
    *,
    intensity: float = 1.0,
) -> None:
    """Apply a sepia tone to an image.

    Args:
        intensity: Blend intensity 0–1. Default 1.0 (full sepia).
    """
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0
    r = arr[..., 0] * 0.393 + arr[..., 1] * 0.769 + arr[..., 2] * 0.189
    g = arr[..., 0] * 0.349 + arr[..., 1] * 0.686 + arr[..., 2] * 0.168
    b = arr[..., 0] * 0.272 + arr[..., 1] * 0.534 + arr[..., 2] * 0.131
    sepia = np.stack([r, g, b], axis=-1).clip(0, 1)
    result = (sepia * intensity + arr * (1.0 - intensity)).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_hdr_simulate(
    input_path: str,
    output_path: str,
    *,
    gamma: float = 0.85,
    unsharp_strength: float = 1.5,
    saturation_boost: float = 1.3,
) -> None:
    """Simulate an HDR look via gamma + local contrast + saturation boost.

    Args:
        gamma: Gamma correction < 1 brightens shadows. Default 0.85.
        unsharp_strength: Local contrast unsharp amount. Default 1.5.
        saturation_boost: Saturation multiplier. Default 1.3.
    """
    from PIL import Image, ImageFilter, ImageEnhance
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0

    # Gamma
    arr = np.power(arr, gamma)

    # Unsharp mask for local contrast
    base = Image.fromarray((arr * 255).clip(0, 255).astype(np.uint8))
    blurred = base.filter(ImageFilter.GaussianBlur(radius=3))
    b_arr = np.array(blurred, dtype=np.float32) / 255.0
    arr = np.clip(arr + unsharp_strength * (arr - b_arr), 0, 1)

    result = Image.fromarray((arr * 255).astype(np.uint8))
    # Saturation boost
    result = ImageEnhance.Color(result).enhance(saturation_boost)
    result.save(output_path)


def image_lens_blur(
    input_path: str,
    output_path: str,
    *,
    blur_radius: int = 15,
    center_x: float = 0.5,
    center_y: float = 0.5,
    focus_radius: float = 0.25,
) -> None:
    """Apply radial lens blur — sharp center, blurred periphery.

    Args:
        blur_radius: Gaussian blur radius for edges. Default 15.
        center_x: Horizontal center 0–1. Default 0.5.
        center_y: Vertical center 0–1. Default 0.5.
        focus_radius: Fraction of image dimension kept sharp. Default 0.25.
    """
    from PIL import Image, ImageFilter
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    w, h = img.size
    cx, cy = int(center_x * w), int(center_y * h)
    focus_r = focus_radius * min(w, h)

    ys, xs = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    # Alpha: 0 at center (use sharp), 1 far out (use blur)
    alpha = np.clip((dist - focus_r) / (focus_r * 2), 0, 1)[..., np.newaxis]

    orig_arr = np.array(img, dtype=np.float32)
    blur_arr = np.array(blurred, dtype=np.float32)
    result = (orig_arr * (1 - alpha) + blur_arr * alpha).clip(0, 255).astype(np.uint8)
    Image.fromarray(result).save(output_path)


def image_cross_process(
    input_path: str,
    output_path: str,
) -> None:
    """Simulate cross-process film look via channel-specific gamma curves."""
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0
    # Red: boosted contrast
    r = np.power(arr[..., 0], 0.7)
    # Green: slight S-curve via gamma
    g = np.power(arr[..., 1], 1.2)
    # Blue: boosted and pushed towards cyan
    b = np.power(arr[..., 2], 0.6) * 0.9 + 0.05
    result = np.stack([r, g, b], axis=-1).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_halftone(
    input_path: str,
    output_path: str,
    *,
    dot_size: int = 8,
) -> None:
    """Apply a halftone dot pattern effect.

    Args:
        dot_size: Cell size in pixels. Default 8.
    """
    from PIL import Image, ImageDraw
    import numpy as np

    img = Image.open(input_path).convert("L")
    w, h = img.size
    arr = np.array(img, dtype=np.float32) / 255.0
    canvas = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(canvas)
    half = dot_size // 2
    for y in range(0, h, dot_size):
        for x in range(0, w, dot_size):
            # Average brightness in cell
            cell = arr[y:y + dot_size, x:x + dot_size]
            if cell.size == 0:
                continue
            brightness = cell.mean()
            radius = int((1 - brightness) * half)
            cx, cy = x + half, y + half
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=0)
    canvas.convert("RGB").save(output_path)


def image_noise(
    input_path: str,
    output_path: str,
    *,
    amount: float = 0.05,
    noise_type: str = "gaussian",
) -> None:
    """Add random noise to an image.

    Args:
        amount: Noise standard deviation (gaussian) or max uniform range, 0–1. Default 0.05.
        noise_type: 'gaussian' or 'uniform'. Default 'gaussian'.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0
    if noise_type == "gaussian":
        noise = np.random.normal(0, amount, arr.shape).astype(np.float32)
    else:
        noise = np.random.uniform(-amount, amount, arr.shape).astype(np.float32)
    result = (arr + noise).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_dither(
    input_path: str,
    output_path: str,
    *,
    colors: int = 16,
) -> None:
    """Reduce image to N colors with Floyd-Steinberg dithering.

    Args:
        colors: Number of colors. Default 16.
    """
    from PIL import Image

    img = Image.open(input_path).convert("RGB")
    # Quantize with dithering
    quantized = img.quantize(colors=colors, dither=Image.Dither.FLOYDSTEINBERG)
    quantized.convert("RGB").save(output_path)


def image_clahe(
    input_path: str,
    output_path: str,
    *,
    clip_limit: float = 2.0,
    tile_size: int = 8,
) -> None:
    """Apply CLAHE (Contrast-Limited Adaptive Histogram Equalization).

    Args:
        clip_limit: Contrast clipping limit. Default 2.0.
        tile_size: Grid tile size. Default 8.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.uint8)

    def _clahe_channel(ch):
        h, w = ch.shape
        th, tw = tile_size, tile_size
        rows = max(1, (h + th - 1) // th)
        cols = max(1, (w + tw - 1) // tw)
        out = np.zeros_like(ch, dtype=np.float32)
        for r in range(rows):
            for c in range(cols):
                y0, y1 = r * th, min((r + 1) * th, h)
                x0, x1 = c * tw, min((c + 1) * tw, w)
                tile = ch[y0:y1, x0:x1]
                hist, bins = np.histogram(tile.ravel(), 256, [0, 256])
                # Clip and redistribute
                excess = np.sum(np.maximum(hist - clip_limit * tile.size / 256, 0))
                hist = np.minimum(hist, clip_limit * tile.size / 256)
                hist += excess / 256
                cdf = hist.cumsum()
                cdf_min = cdf[cdf > 0][0] if cdf[cdf > 0].size else 1
                total = tile.size
                lut = np.clip(np.round((cdf - cdf_min) / (total - cdf_min) * 255), 0, 255).astype(np.uint8)
                out[y0:y1, x0:x1] = lut[tile]
        return out.clip(0, 255).astype(np.uint8)

    result = np.stack([_clahe_channel(arr[..., i]) for i in range(3)], axis=-1)
    Image.fromarray(result).save(output_path)


def image_palette_swap(
    input_path: str,
    output_path: str,
    palette: list[tuple[int, int, int]],
) -> None:
    """Map every pixel to the nearest color in the provided palette.

    Args:
        palette: List of RGB tuples to use as the target palette.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float32)
    pal = np.array(palette, dtype=np.float32)  # (N, 3)
    # Compute nearest palette color per pixel
    flat = arr.reshape(-1, 3)  # (H*W, 3)
    diffs = flat[:, np.newaxis, :] - pal[np.newaxis, :, :]  # (H*W, N, 3)
    idx = np.argmin((diffs ** 2).sum(axis=-1), axis=1)  # (H*W,)
    result = pal[idx].reshape(arr.shape).astype(np.uint8)
    Image.fromarray(result).save(output_path)


def image_channel_split(
    input_path: str,
    output_r: str,
    output_g: str,
    output_b: str,
) -> None:
    """Split an RGB image into three grayscale channel images.

    Args:
        output_r: Path for the red-channel image.
        output_g: Path for the green-channel image.
        output_b: Path for the blue-channel image.
    """
    from PIL import Image

    img = Image.open(input_path).convert("RGB")
    r, g, b = img.split()
    r.save(output_r)
    g.save(output_g)
    b.save(output_b)


def image_channel_merge(
    input_r: str,
    input_g: str,
    input_b: str,
    output_path: str,
) -> None:
    """Merge three grayscale images into a single RGB image.

    Args:
        input_r: Path to red-channel image.
        input_g: Path to green-channel image.
        input_b: Path to blue-channel image.
    """
    from PIL import Image

    r = Image.open(input_r).convert("L")
    g = Image.open(input_g).convert("L")
    b = Image.open(input_b).convert("L")
    Image.merge("RGB", (r, g, b)).save(output_path)


def image_blend_overlay(
    base_path: str,
    blend_path: str,
    output_path: str,
    *,
    opacity: float = 1.0,
) -> None:
    """Blend two images using the Overlay blend mode.

    Overlay = 2*a*b if a<0.5, else 1-2*(1-a)*(1-b).

    Args:
        opacity: Blend strength 0–1. Default 1.0.
    """
    from PIL import Image
    import numpy as np

    base = Image.open(base_path).convert("RGB")
    blend = Image.open(blend_path).convert("RGB").resize(base.size, Image.LANCZOS)
    a = np.array(base, dtype=np.float32) / 255.0
    b = np.array(blend, dtype=np.float32) / 255.0
    overlay = np.where(a < 0.5, 2 * a * b, 1 - 2 * (1 - a) * (1 - b))
    result = (a * (1 - opacity) + overlay * opacity).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_blend_multiply(
    base_path: str,
    blend_path: str,
    output_path: str,
    *,
    opacity: float = 1.0,
) -> None:
    """Blend two images using the Multiply blend mode.

    Args:
        opacity: Blend strength 0–1. Default 1.0.
    """
    from PIL import Image
    import numpy as np

    base = Image.open(base_path).convert("RGB")
    blend = Image.open(blend_path).convert("RGB").resize(base.size, Image.LANCZOS)
    a = np.array(base, dtype=np.float32) / 255.0
    b = np.array(blend, dtype=np.float32) / 255.0
    mult = a * b
    result = (a * (1 - opacity) + mult * opacity).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_blend_screen(
    base_path: str,
    blend_path: str,
    output_path: str,
    *,
    opacity: float = 1.0,
) -> None:
    """Blend two images using the Screen blend mode.

    Screen = 1 - (1-a)*(1-b).

    Args:
        opacity: Blend strength 0–1. Default 1.0.
    """
    from PIL import Image
    import numpy as np

    base = Image.open(base_path).convert("RGB")
    blend = Image.open(blend_path).convert("RGB").resize(base.size, Image.LANCZOS)
    a = np.array(base, dtype=np.float32) / 255.0
    b = np.array(blend, dtype=np.float32) / 255.0
    screen = 1.0 - (1.0 - a) * (1.0 - b)
    result = (a * (1 - opacity) + screen * opacity).clip(0, 1)
    Image.fromarray((result * 255).astype(np.uint8)).save(output_path)


def image_pixelate_region(
    input_path: str,
    output_path: str,
    *,
    x: int = 0,
    y: int = 0,
    width: int = 50,
    height: int = 50,
    block_size: int = 10,
) -> None:
    """Pixelate a rectangular region of an image (for privacy/censorship).

    Args:
        x: X offset of region. Default 0.
        y: Y offset of region. Default 0.
        width: Region width in pixels. Default 50.
        height: Region height in pixels. Default 50.
        block_size: Pixel block size. Default 10.
    """
    from PIL import Image

    img = Image.open(input_path).convert("RGB")
    region = img.crop((x, y, x + width, y + height))
    # Downscale then upscale to pixelate
    small = region.resize(
        (max(1, width // block_size), max(1, height // block_size)),
        Image.NEAREST,
    )
    pixelated = small.resize((width, height), Image.NEAREST)
    img.paste(pixelated, (x, y))
    img.save(output_path)


def image_text_overlay(
    input_path: str,
    output_path: str,
    text: str,
    *,
    x: int = 10,
    y: int = 10,
    font_size: int = 20,
    color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Draw text onto an image.

    Args:
        text: Text string to draw.
        x: X position. Default 10.
        y: Y position. Default 10.
        font_size: Font size in points. Default 20.
        color: RGB color tuple. Default white.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(input_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()
    draw.text((x, y), text, fill=color, font=font)
    img.save(output_path)


def image_draw_rect(
    input_path: str,
    output_path: str,
    *,
    x: int = 10,
    y: int = 10,
    width: int = 50,
    height: int = 50,
    color: tuple[int, int, int] = (255, 0, 0),
    fill: bool = False,
    line_width: int = 2,
) -> None:
    """Draw a rectangle onto an image.

    Args:
        x: X offset. Default 10.
        y: Y offset. Default 10.
        width: Rectangle width. Default 50.
        height: Rectangle height. Default 50.
        color: RGB color. Default red.
        fill: Fill rectangle if True. Default False (outline only).
        line_width: Outline width in pixels. Default 2.
    """
    from PIL import Image, ImageDraw

    img = Image.open(input_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    bbox = [x, y, x + width, y + height]
    if fill:
        draw.rectangle(bbox, fill=color)
    else:
        draw.rectangle(bbox, outline=color, width=line_width)
    img.save(output_path)


def image_histogram_equalize(
    input_path: str,
    output_path: str,
) -> None:
    """Apply global histogram equalization per channel for contrast enhancement."""
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.uint8)

    def _equalize(ch):
        hist, _ = np.histogram(ch.ravel(), 256, [0, 256])
        cdf = hist.cumsum()
        cdf_min = cdf[cdf > 0][0]
        total = ch.size
        lut = np.clip(np.round((cdf - cdf_min) / (total - cdf_min) * 255), 0, 255).astype(np.uint8)
        return lut[ch]

    result = np.stack([_equalize(arr[..., i]) for i in range(3)], axis=-1)
    Image.fromarray(result).save(output_path)


def image_mosaic(
    input_path: str,
    output_path: str,
    *,
    tile_width: int = 50,
    tile_height: int = 50,
    cols: int = 5,
    rows: int = 5,
) -> None:
    """Create a mosaic by tiling a thumbnail of the image across a grid.

    Args:
        tile_width: Width of each tile. Default 50.
        tile_height: Height of each tile. Default 50.
        cols: Number of columns. Default 5.
        rows: Number of rows. Default 5.
    """
    from PIL import Image

    img = Image.open(input_path).convert("RGB")
    tile = img.resize((tile_width, tile_height), Image.LANCZOS)
    canvas = Image.new("RGB", (cols * tile_width, rows * tile_height))
    for r in range(rows):
        for c in range(cols):
            canvas.paste(tile, (c * tile_width, r * tile_height))
    canvas.save(output_path)
