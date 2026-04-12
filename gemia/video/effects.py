"""Video effects: lut_apply, chroma_aberration, vhs_effect, color_wheels, zoom_pan, chroma_warp."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr}")


def lut_apply(input_path: str, output_path: str, *, lut_path: str, intensity: float = 1.0) -> str:
    """Apply a .cube 3D LUT file to a video.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        lut_path: Path to a .cube LUT file.
        intensity: Blend between original (0.0) and LUT-applied (1.0) output.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    intensity = max(0.0, min(1.0, intensity))
    if intensity >= 1.0:
        vf = f"lut3d=file='{lut_path}'"
    else:
        # blend original with graded
        vf = f"split[orig][lut];[lut]lut3d=file='{lut_path}'[graded];[orig][graded]blend=all_expr='A*{1-intensity}+B*{intensity}'"
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


def chroma_aberration(input_path: str, output_path: str, *, strength: int = 2) -> str:
    """Apply chromatic aberration (RGB channel shift) to a video.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        strength: Pixel offset for R/B channel shift. Default 2.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    s = int(strength)
    # Shift red channel right/down, blue channel left/up; green stays
    vf = (
        f"split=3[r][g][b];"
        f"[r]rgbashift=rh={s}:rv={s}[rs];"
        f"[b]rgbashift=bh=-{s}:bv=-{s}[bs];"
        f"[rs][g][bs]mergeplanes=0x001020:gbrp"
    )
    # Simpler approach using geq per channel
    w, h = "iw", "ih"
    vf = (
        f"geq="
        f"r='r(X-{s},Y-{s})':"
        f"g='g(X,Y)':"
        f"b='b(X+{s},Y+{s})'"
    )
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


def vhs_effect(input_path: str, output_path: str, *, strength: float = 0.5) -> str:
    """Apply a VHS/retro look (color bleed, scan lines, noise).

    Args:
        input_path: Input video path.
        output_path: Output video path.
        strength: Effect intensity 0.0–1.0.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    s = max(0.0, min(1.0, strength))
    noise_str = int(s * 40)
    blur_x = max(1, int(s * 3))
    # Scan line alpha depends on strength
    scan_alpha = s * 0.3

    vf_parts = [
        # slight horizontal blur (chroma bleed)
        f"boxblur={blur_x}:0",
        # add noise
        f"noise=alls={noise_str}:allf=t+u",
        # scan lines via drawgrid
        f"drawgrid=width=0:height=2:thickness=1:color=black@{scan_alpha:.2f}",
        # desaturate slightly
        f"hue=s={1.0 - s * 0.3:.2f}",
    ]
    vf = ",".join(vf_parts)
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


def color_wheels(input_path: str, output_path: str, *,
                 lift: tuple[float, float, float] = (0.0, 0.0, 0.0),
                 gamma: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 gain: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> str:
    """3-way color wheel adjustment (lift/gamma/gain) for shadows/mids/highlights.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        lift: (r, g, b) additive offset for shadows [-0.5, 0.5].
        gamma: (r, g, b) gamma curve for midtones [0.1, 4.0], 1.0=neutral.
        gain: (r, g, b) multiplicative scale for highlights [0.0, 4.0].

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    lr, lg, lb = lift
    gr, gg, gb = gamma
    kr, kg, kb = gain

    def ch_expr(lift_v: float, gamma_v: float, gain_v: float, ch: str) -> str:
        g = max(gamma_v, 1e-7)
        return f"clip(pow(clip({ch}(X,Y)/255*{gain_v}+{lift_v},0,1),1/{g})*255,0,255)"

    vf = (
        f"geq="
        f"r='{ch_expr(lr, gr, kr, 'r')}':"
        f"g='{ch_expr(lg, gg, kg, 'g')}':"
        f"b='{ch_expr(lb, gb, kb, 'b')}'"
    )
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


def chroma_warp(
    input_path: str,
    output_path: str,
    *,
    hue_shift: float = 30.0,
    saturation_boost: float = 1.5,
) -> str:
    """Warp hue and saturation of video colours.

    Mirrors DaVinci Resolve's *Colour Warper*: globally shifts hue and boosts
    saturation. For selective hue-band warping use colorslice_grade instead.

    Args:
        input_path: Source video or image.
        output_path: Destination path.
        hue_shift: Degrees to rotate all hues. Default 30.
        saturation_boost: Saturation multiplier. Default 1.5.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vf = f"hue=h={hue_shift:.1f}:s={saturation_boost:.2f}"
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


def zoom_pan(input_path: str, output_path: str, *,
             start_zoom: float = 1.0, end_zoom: float = 1.5,
             x: float = 0.5, y: float = 0.5) -> str:
    """Ken Burns zoom-pan effect: animate zoom from start_zoom to end_zoom.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        start_zoom: Zoom factor at the start (1.0 = no zoom).
        end_zoom: Zoom factor at the end.
        x: Horizontal anchor point [0.0=left .. 1.0=right]. Default 0.5 (center).
        y: Vertical anchor point [0.0=top .. 1.0=bottom]. Default 0.5 (center).

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    sz = max(start_zoom, 0.1)
    ez = max(end_zoom, 0.1)
    # zoompan: z=zoom, x/y=top-left corner of crop window
    # N = frame number, duration = total frames (use on(1) for first frame duration)
    zoom_expr = f"'if(eq(on,1),{sz},zoom+({ez}-{sz})/({{'on':'on'}}))'"
    # Simpler: use linear interpolation with 'on' and total frame count
    # zoompan requires knowing total frames; use a large value and it'll just hold
    zoom_expr = f"{sz}+on*({ez}-{sz})/max(1,({ez}-{sz})*10000)"
    # Use proper zoompan: z progresses from sz to ez over the clip
    # x,y in zoompan are offsets in the *scaled* image for the crop window
    px = x
    py = y
    vf = (
        f"zoompan="
        f"z='min({sz}+on/max(1,{{'dur'}})*({ez}-{sz}),{max(sz,ez)})':"
        f"x='iw/2-(iw/zoom/2)+{px}*(iw-iw/zoom)':"
        f"y='ih/2-(ih/zoom/2)+{py}*(ih-ih/zoom)':"
        f"d=1:s=iw×ih:fps=30"
    )
    # Actually, simplest reliable zoompan:
    vf = (
        f"scale=8000:-1,zoompan="
        f"z='if(lte(on,1),{sz},min(zoom+0.0005,{ez}))':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d=1:s=iw/8:fps=30,scale=iw:-1"
    )
    # Let's use a clean approach based on input resolution
    import json
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True,
    )
    info = json.loads(probe.stdout)
    vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    w = vstream.get("width", 1280)
    h = vstream.get("height", 720)

    # frames per second
    fps_str = vstream.get("r_frame_rate", "30/1")
    num, den = (int(v) for v in fps_str.split("/"))
    fps = num / den if den else 30.0

    step = (ez - sz) / max((fps * 10), 1)  # spread over full clip (up to 10s)
    vf = (
        f"zoompan="
        f"z='min(if(lte(on,1),{sz},zoom+{step:.8f}),{ez})':"
        f"x='iw/2-(iw/zoom/2)+({px}-0.5)*(iw-iw/zoom)*2':"
        f"y='ih/2-(ih/zoom/2)+({py}-0.5)*(ih-ih/zoom)*2':"
        f"d=1:s={w}x{h}:fps={fps}"
    )
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path
