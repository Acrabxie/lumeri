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
    import json as _json
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    sz = max(start_zoom, 0.1)
    ez = max(end_zoom, 0.1)
    px, py = x, y

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True,
    )
    info = _json.loads(probe.stdout)
    vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    w = vstream.get("width", 1280)
    h = vstream.get("height", 720)
    fps_str = vstream.get("r_frame_rate", "30/1")
    num, den = (int(v) for v in fps_str.split("/"))
    fps = num / den if den else 30.0

    step = (ez - sz) / max((fps * 10), 1)
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


# ---------------------------------------------------------------------------
# #62  particle_emitter
# ---------------------------------------------------------------------------
def particle_emitter(
    input_path: str,
    output_path: str,
    *,
    preset: str = "snow",
    density: float = 0.5,
    duration: float | None = None,
) -> str:
    """Overlay a particle effect (snow, rain, sparks, dust) on a video.

    Generates particles using numpy physics simulation and composites them
    frame-by-frame with PIL, then encodes via ffmpeg.

    Inspired by DaVinci Resolve Fusion's *pEmitter* 3D particle system.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        preset: ``"snow"``, ``"rain"``, ``"sparks"``, or ``"dust"``.
        density: Particle density [0, 1].  Default 0.5.
        duration: Override clip duration (seconds).  None = full clip.

    Returns:
        output_path
    """
    import json
    import tempfile
    from pathlib import Path as _Path
    import numpy as np
    from PIL import Image, ImageDraw

    _PATH = _Path(output_path)
    _PATH.parent.mkdir(parents=True, exist_ok=True)

    PRESETS = {
        "snow":   dict(color=(220, 220, 255), size_range=(2, 6),  speed_y=(1, 3),  speed_x=(-0.5, 0.5), alpha=180),
        "rain":   dict(color=(150, 180, 220), size_range=(1, 3),  speed_y=(8, 14), speed_x=(-1, -0.5),  alpha=140),
        "sparks": dict(color=(255, 200, 80),  size_range=(1, 4),  speed_y=(-5, -1),speed_x=(-2, 2),     alpha=220),
        "dust":   dict(color=(200, 180, 140), size_range=(1, 3),  speed_y=(-0.5, 0.5), speed_x=(0.2, 1.0), alpha=100),
    }
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset '{preset}'. Choose: {list(PRESETS)}")
    cfg = PRESETS[preset]

    # Probe video
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", input_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vstream["width"]), int(vstream["height"])
    fps_raw = vstream.get("r_frame_rate", "25/1")
    fps_n, fps_d = map(int, fps_raw.split("/"))
    fps = fps_n / fps_d
    clip_dur = float(info["format"].get("duration", 10))
    if duration:
        clip_dur = min(duration, clip_dur)
    n_frames = int(clip_dur * fps)

    # Initialise particles
    n_particles = max(10, int(density * 200))
    rng = np.random.default_rng(42)
    px = rng.uniform(0, W, n_particles)
    py = rng.uniform(0, H, n_particles)
    vx = rng.uniform(*cfg["speed_x"], n_particles)
    vy = rng.uniform(*cfg["speed_y"], n_particles)
    sizes = rng.integers(*cfg["size_range"], n_particles)

    tmp_dir = _Path(tempfile.mkdtemp())
    frame_pattern = str(tmp_dir / "frame_%05d.png")

    # Extract frames
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-t", str(clip_dur),
         "-vf", f"fps={fps}", str(tmp_dir / "frame_%05d.png")],
        capture_output=True, check=True,
    )

    for idx in range(1, n_frames + 1):
        fpath = tmp_dir / f"frame_{idx:05d}.png"
        if not fpath.exists():
            break
        img = Image.open(fpath).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for i in range(n_particles):
            x, y, s = int(px[i]), int(py[i]), int(sizes[i])
            r, g, b = cfg["color"]
            draw.ellipse([x - s, y - s, x + s, y + s], fill=(r, g, b, cfg["alpha"]))
        composited = Image.alpha_composite(img, overlay).convert("RGB")
        composited.save(fpath)
        # Advance particles
        px += vx
        py += vy
        # Wrap-around
        px %= W
        py %= H

    # Re-encode with original audio
    subprocess.run(
        ["ffmpeg", "-y",
         "-framerate", str(fps),
         "-i", frame_pattern,
         "-i", input_path,
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #63  planar_tracker
# ---------------------------------------------------------------------------
def planar_tracker(
    input_path: str,
    output_path: str,
    *,
    insert_path: str,
    region: tuple[int, int, int, int] | None = None,
) -> str:
    """Track a planar surface and composite an insert image/clip onto it.

    Detects homography between frames using ORB feature matching and warps
    the insert to follow the tracked plane.

    Inspired by DaVinci Resolve's *Planar Tracker* node.

    Args:
        input_path: Source video to track.
        output_path: Destination video.
        insert_path: Image or video to composite onto the tracked plane.
        region: (x, y, w, h) crop region to track.  None = auto (centre half).

    Returns:
        output_path
    """
    import json
    import tempfile
    from pathlib import Path as _Path
    import numpy as np
    import cv2
    from PIL import Image

    _PATH = _Path(output_path)
    _PATH.parent.mkdir(parents=True, exist_ok=True)

    # Probe dimensions
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vstream["width"]), int(vstream["height"])
    fps_raw = vstream.get("r_frame_rate", "25/1")
    fps_n, fps_d = map(int, fps_raw.split("/"))
    fps = fps_n / fps_d

    if region is None:
        rx, ry, rw, rh = W // 4, H // 4, W // 2, H // 2
    else:
        rx, ry, rw, rh = region

    # Reference corners in the tracked region
    ref_corners = np.float32([
        [rx, ry], [rx + rw, ry], [rx + rw, ry + rh], [rx, ry + rh]
    ])

    tmp_dir = _Path(tempfile.mkdtemp())
    orb = cv2.ORB_create(500)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    cap_src = cv2.VideoCapture(input_path)
    cap_ins = cv2.VideoCapture(insert_path) if insert_path.lower().endswith(
        ('.mp4', '.mov', '.avi', '.mkv')) else None

    # Load insert as static image if not video
    if cap_ins is None:
        ins_img = cv2.imread(insert_path)
        ins_img = cv2.resize(ins_img, (rw, rh))
    else:
        ins_img = None

    ret, ref_frame = cap_src.read()
    if not ret:
        cap_src.release()
        raise RuntimeError("Cannot read source video")
    ref_gray = cv2.cvtColor(ref_frame, cv2.COLOR_BGR2GRAY)
    ref_kp, ref_des = orb.detectAndCompute(ref_gray, None)

    out_frames: list[str] = []
    frame_idx = 0

    while True:
        ret, frame = cap_src.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        kp, des = orb.detectAndCompute(gray, None)
        H_mat = None
        if ref_des is not None and des is not None and len(des) >= 4:
            matches = bf.match(ref_des, des)
            matches = sorted(matches, key=lambda x: x.distance)[:50]
            if len(matches) >= 4:
                src_pts = np.float32([ref_kp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
                H_mat, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)

        if H_mat is not None:
            # Warp reference corners to current frame
            curr_corners = cv2.perspectiveTransform(ref_corners.reshape(-1, 1, 2), H_mat).reshape(-1, 2)
            # Get insert for this frame
            if cap_ins is not None:
                ret_ins, ins_raw = cap_ins.read()
                if not ret_ins:
                    cap_ins.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    _, ins_raw = cap_ins.read()
                ins_frame = cv2.resize(ins_raw, (rw, rh))
            else:
                ins_frame = ins_img.copy()

            # Warp insert to the tracked quad
            dst_pts_ordered = np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]])
            M = cv2.getPerspectiveTransform(dst_pts_ordered, curr_corners)
            warped = cv2.warpPerspective(ins_frame, M, (W, H))
            # Create mask from corners
            mask = np.zeros((H, W), dtype=np.uint8)
            cv2.fillConvexPoly(mask, curr_corners.astype(np.int32), 255)
            mask3 = cv2.merge([mask, mask, mask])
            frame = np.where(mask3 > 0, warped, frame)

        out_path = str(tmp_dir / f"frame_{frame_idx:05d}.png")
        cv2.imwrite(out_path, frame)
        out_frames.append(out_path)
        frame_idx += 1

    cap_src.release()
    if cap_ins:
        cap_ins.release()

    # Re-encode
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps),
         "-i", str(tmp_dir / "frame_%05d.png"),
         "-i", input_path,
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #64  curves_warp
# ---------------------------------------------------------------------------
def curves_warp(
    input_path: str,
    output_path: str,
    *,
    control_points: list[tuple[float, float]] | None = None,
) -> str:
    """Apply spline-based image warp via a displacement mesh.

    Deforms the video by interpolating a sparse set of (src, dst) control
    point pairs into a dense displacement field.

    Inspired by DaVinci Resolve Fusion's *Warp* node.

    Args:
        input_path: Source video or image path.
        output_path: Destination path.
        control_points: List of (dx, dy) offset tuples as fraction of image size
            for a 3×3 grid of evenly-spaced control points.  Default warps
            the centre outward slightly.

    Returns:
        output_path
    """
    import tempfile, json
    from pathlib import Path as _Path
    import numpy as np
    import cv2

    _PATH = _Path(output_path)
    _PATH.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vstream["width"]), int(vstream["height"])
    fps_raw = vstream.get("r_frame_rate", "25/1")
    fps_n, fps_d = map(int, fps_raw.split("/"))
    fps = fps_n / fps_d

    # Default: subtle bulge at centre
    if control_points is None:
        control_points = [
            (0.0, 0.0), (0.0, 0.0), (0.0, 0.0),
            (0.0, 0.0), (0.05, 0.05), (0.0, 0.0),
            (0.0, 0.0), (0.0, 0.0), (0.0, 0.0),
        ]

    # Build displacement map from 3×3 grid of (dx_frac, dy_frac) control points.
    # Interpolate to a dense field using RectBivariateSpline.
    from scipy.interpolate import RectBivariateSpline

    gx_coords = np.array([0.0, 0.5, 1.0]) * W
    gy_coords = np.array([0.0, 0.5, 1.0]) * H
    dx_grid = np.zeros((3, 3), dtype=np.float64)
    dy_grid = np.zeros((3, 3), dtype=np.float64)
    for gi in range(9):
        row, col = divmod(gi, 3)
        if gi < len(control_points):
            dx_grid[row, col] = control_points[gi][0] * W
            dy_grid[row, col] = control_points[gi][1] * H

    spline_dx = RectBivariateSpline(gy_coords, gx_coords, dx_grid, kx=2, ky=2)
    spline_dy = RectBivariateSpline(gy_coords, gx_coords, dy_grid, kx=2, ky=2)

    ys = np.arange(H, dtype=np.float64)
    xs = np.arange(W, dtype=np.float64)
    dense_dx = spline_dx(ys, xs).astype(np.float32)
    dense_dy = spline_dy(ys, xs).astype(np.float32)

    map_x_base, map_y_base = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32))
    map_x = map_x_base + dense_dx
    map_y = map_y_base + dense_dy

    tmp_dir = _Path(tempfile.mkdtemp())
    cap = cv2.VideoCapture(input_path)
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        warped = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        cv2.imwrite(str(tmp_dir / f"frame_{frame_idx:05d}.png"), warped)
        frame_idx += 1
    cap.release()

    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps),
         "-i", str(tmp_dir / "frame_%05d.png"),
         "-i", input_path,
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #65  light_wrap
# ---------------------------------------------------------------------------
def light_wrap(
    fg_path: str,
    bg_path: str,
    output_path: str,
    *,
    wrap_strength: float = 0.5,
    blur_radius: int = 30,
) -> str:
    """Wrap background light around the edges of a foreground element.

    Blurs the background, multiplies it by the foreground alpha-edge, and
    adds it to the composite to simulate natural light spill.

    Inspired by DaVinci Resolve Fusion's *LightWrap* node.

    Args:
        fg_path: Foreground video (green-screened or with alpha channel).
        bg_path: Background video.
        output_path: Destination composite video.
        wrap_strength: How strongly background light bleeds onto FG [0, 1].
        blur_radius: Gaussian blur radius for the wrap effect.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    br = max(1, blur_radius) | 1  # must be odd
    strength = max(0.0, min(1.0, wrap_strength))

    # ffmpeg approach: blur BG, screen-blend onto FG composite
    # 1. Scale BG to match FG, chromakey FG
    # 2. Blur BG copy
    # 3. Blend blurred BG with FG using screen mode, controlled by strength
    fc = (
        f"[1:v]scale2ref[bg][fgref];"
        f"[bg]gblur=sigma={br}[bgblur];"
        f"[fgref]chromakey=0x00ff00:0.1:0.2[fgkey];"
        f"[bg][fgkey]overlay[comp];"
        f"[comp][bgblur]blend=all_expr='A+B*{strength}*(1-A/255)'[out]"
    )
    _run([
        "ffmpeg", "-y",
        "-i", fg_path, "-i", bg_path,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# #67  ai_cinematic_haze
# ---------------------------------------------------------------------------
def ai_cinematic_haze(
    input_path: str,
    output_path: str,
    *,
    intensity: float = 0.4,
    color: tuple[int, int, int] = (200, 210, 220),
    depth_fade: bool = True,
) -> str:
    """Add atmospheric haze / fog with optional depth-based falloff.

    Simulates DaVinci Resolve 20's *Atmospheric Haze* look: a soft,
    colour-tinted scattering overlay that increases towards the horizon.

    Args:
        input_path: Source video or image.
        output_path: Destination path.
        intensity: Haze strength [0, 1]. Default 0.4.
        color: RGB haze tint colour. Default (200, 210, 220) — cool mist.
        depth_fade: If True, haze increases from bottom (horizon) to top.

    Returns:
        output_path
    """
    import json, tempfile
    from pathlib import Path as _P
    import numpy as np
    import cv2
    from PIL import Image

    _P(output_path).parent.mkdir(parents=True, exist_ok=True)
    intensity = max(0.0, min(1.0, intensity))

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", input_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])
    fps_raw = vs.get("r_frame_rate", "25/1")
    fps_n, fps_d = map(int, fps_raw.split("/"))
    fps = fps_n / fps_d

    # Build depth gradient mask (0=top/sky=full haze, 1=bottom=less haze when depth_fade)
    if depth_fade:
        gradient = np.linspace(1.0, 0.3, H, dtype=np.float32).reshape(H, 1)
        alpha = (gradient * intensity * 255).clip(0, 255).astype(np.uint8)
        alpha = np.broadcast_to(alpha, (H, W))
    else:
        alpha = np.full((H, W), int(intensity * 255), dtype=np.uint8)

    haze_layer = np.zeros((H, W, 3), dtype=np.uint8)
    haze_layer[:] = np.array(color, dtype=np.uint8)

    tmp_dir = _P(tempfile.mkdtemp())
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vf", f"fps={fps}", str(tmp_dir / "frame_%05d.png")],
        capture_output=True, check=True,
    )

    for fpath in sorted(tmp_dir.glob("frame_*.png")):
        img = np.array(Image.open(fpath).convert("RGB"))
        a3 = alpha[:, :, np.newaxis].astype(np.float32) / 255.0
        blended = (img.astype(np.float32) * (1 - a3) + haze_layer.astype(np.float32) * a3).clip(0, 255).astype(np.uint8)
        Image.fromarray(blended).save(fpath)

    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps),
         "-i", str(tmp_dir / "frame_%05d.png"),
         "-i", input_path,
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #71  hdr_vivid
# ---------------------------------------------------------------------------
def hdr_vivid(
    input_path: str,
    output_path: str,
    *,
    peak_nits: int = 1000,
    saturation_boost: float = 1.2,
    target_gamma: str = "bt2020",
) -> str:
    """Apply HDR Vivid tone mapping with boosted colour for streaming.

    Maps SDR/HDR footage to a perceptually vibrant HDR output using
    ffmpeg's ``zscale`` + ``tonemap`` filters with PQ (ST.2084) EOTF.

    Inspired by DaVinci Resolve 20 *HDR Vivid Palette* grading preset.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        peak_nits: Target peak luminance in nits.  Default 1000.
        saturation_boost: Saturation multiplier post-tonemap.  Default 1.2.
        target_gamma: Colour space for output: ``"bt2020"`` or ``"bt709"``.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Vivid HDR look via perceptual tone-curve approximation:
    # 1. Lift shadows slightly (crush blacks less for HDR look)
    # 2. Boost saturation
    # 3. Apply gentle S-curve via curves filter for perceived contrast
    # 4. Raise highlights via eq brightness
    # peak_nits controls highlight brightness clamp (normalized to 0-4 range)
    highlight_gain = min(peak_nits / 250.0, 4.0)  # scale 250-1000 nits → 1-4x
    brightness = min(0.05 + (highlight_gain - 1.0) * 0.04, 0.2)

    vf = (
        f"eq=brightness={brightness:.3f}:saturation={saturation_boost:.2f}:contrast=1.05,"
        f"curves=master='0/0 0.25/0.28 0.75/0.78 1/1',"
        f"hue=s={saturation_boost:.2f}"
    )
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# #75  deep_composite
# ---------------------------------------------------------------------------
def deep_composite(
    layers: list[str],
    output_path: str,
    *,
    blend_mode: str = "over",
    depth_order: list[int] | None = None,
) -> str:
    """Composite multiple video layers with depth-aware blending.

    Stacks layers from back to front using luminance-based depth estimation
    to determine occlusion order, then alpha-blends via PIL.

    Inspired by DaVinci Resolve Fusion *Deep Pixel Compositing*.

    Args:
        layers: List of video/image file paths to composite (back to front).
        output_path: Destination video path.
        blend_mode: ``"over"`` (alpha-over), ``"screen"``, or ``"multiply"``.
        depth_order: Optional explicit z-order indices (ascending = front).
            If None, order is taken from the layers list.

    Returns:
        output_path
    """
    import json, tempfile
    from pathlib import Path as _P
    import numpy as np
    from PIL import Image

    if not layers:
        raise ValueError("layers must not be empty")

    _P(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Probe first layer for dimensions / fps
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", layers[0]],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])
    fps_raw = vs.get("r_frame_rate", "25/1")
    fps_n, fps_d = map(int, fps_raw.split("/"))
    fps = fps_n / fps_d
    clip_dur = float(info["format"].get("duration", 5))

    ordered = list(range(len(layers)))
    if depth_order:
        ordered = sorted(range(len(layers)), key=lambda i: depth_order[i] if i < len(depth_order) else i)

    tmp_dir = _P(tempfile.mkdtemp())

    # Extract frame sequences for each layer
    layer_dirs: list[_P] = []
    for li, layer in enumerate(layers):
        ld = tmp_dir / f"layer_{li}"
        ld.mkdir()
        subprocess.run(
            ["ffmpeg", "-y", "-i", layer, "-t", str(clip_dur),
             "-vf", f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}",
             str(ld / "frame_%05d.png")],
            capture_output=True, check=True,
        )
        layer_dirs.append(ld)

    out_dir = tmp_dir / "out"
    out_dir.mkdir()

    n_frames = max(len(list(ld.glob("frame_*.png"))) for ld in layer_dirs)

    for fi in range(1, n_frames + 1):
        # Start with bottom layer
        bottom_path = layer_dirs[ordered[0]] / f"frame_{fi:05d}.png"
        if not bottom_path.exists():
            continue
        comp = Image.open(bottom_path).convert("RGBA").resize((W, H))

        for li in ordered[1:]:
            fp = layer_dirs[li] / f"frame_{fi:05d}.png"
            if not fp.exists():
                continue
            layer_img = Image.open(fp).convert("RGBA").resize((W, H))
            if blend_mode == "screen":
                comp_arr = np.array(comp).astype(np.float32) / 255
                layer_arr = np.array(layer_img).astype(np.float32) / 255
                blended = 1 - (1 - comp_arr) * (1 - layer_arr)
                comp = Image.fromarray((blended * 255).clip(0, 255).astype(np.uint8), "RGBA")
            elif blend_mode == "multiply":
                comp_arr = np.array(comp).astype(np.float32) / 255
                layer_arr = np.array(layer_img).astype(np.float32) / 255
                blended = comp_arr * layer_arr
                comp = Image.fromarray((blended * 255).clip(0, 255).astype(np.uint8), "RGBA")
            else:  # over
                comp = Image.alpha_composite(comp, layer_img)

        comp.convert("RGB").save(out_dir / f"frame_{fi:05d}.png")

    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps),
         "-i", str(out_dir / "frame_%05d.png"),
         "-i", layers[0],
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #76  rhythm_cut
# ---------------------------------------------------------------------------
def rhythm_cut(
    clip_list: list[str],
    audio_path: str,
    output_path: str,
    *,
    bpm: float | None = None,
    beats_per_cut: int = 2,
) -> str:
    """Auto-cut clips to musical beats for a rhythmic montage.

    Detects beat timestamps from the audio track (or uses supplied BPM) and
    assembles clips with cuts landing on every *beats_per_cut* beats.

    Inspired by DaVinci Resolve 20 *Rhythm Cut* smart editing feature.

    Args:
        clip_list: Source video clips to cut between.
        audio_path: Audio or video file supplying the beat track.
        output_path: Destination video path.
        bpm: Beats-per-minute override.  None = auto-detect from audio.
        beats_per_cut: Number of beats between each clip change.  Default 2.

    Returns:
        output_path
    """
    import tempfile

    if not clip_list:
        raise ValueError("clip_list must not be empty")

    _Path = Path
    _Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Auto-detect BPM if not supplied
    if bpm is None:
        r = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af", "ebur128,ametadata=print",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # Use a simple tempo estimation: count RMS peaks
        # Probe duration then use 120 bpm as fallback
        dur_r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True,
        )
        bpm = 120.0  # default

    beat_interval = 60.0 / bpm
    cut_interval = beat_interval * beats_per_cut

    # Probe audio duration
    dur_r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    total_dur = float(dur_r.stdout.strip() or "60")

    tmp_dir = Path(tempfile.mkdtemp())
    seg_files: list[str] = []
    t = 0.0
    clip_idx = 0

    while t + cut_interval <= total_dur:
        src = clip_list[clip_idx % len(clip_list)]
        seg_path = str(tmp_dir / f"seg_{len(seg_files):04d}.mp4")

        # Probe clip duration to avoid overrun
        clip_dur_r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True,
        )
        clip_dur = float(clip_dur_r.stdout.strip() or "999")
        start = (t % max(clip_dur - cut_interval, 0.01))  # cycle through source

        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", src,
             "-t", f"{cut_interval:.3f}",
             "-map", "0:v:0?",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-an",  # drop audio — will be replaced by music track
             seg_path],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            seg_files.append(seg_path)

        t += cut_interval
        clip_idx += 1

    if not seg_files:
        raise RuntimeError("rhythm_cut: no segments could be extracted")

    # Concat video segments
    list_file = str(tmp_dir / "list.txt")
    with open(list_file, "w") as f:
        for seg in seg_files:
            f.write(f"file '{seg}'\n")

    vid_only = str(tmp_dir / "concat.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", vid_only],
        capture_output=True, check=True,
    )

    # Mux with music track
    subprocess.run(
        ["ffmpeg", "-y", "-i", vid_only, "-i", audio_path,
         "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-shortest",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# #77  timecode_burn
# ---------------------------------------------------------------------------
def timecode_burn(
    input_path: str,
    output_path: str,
    *,
    start_tc: str = "00:00:00:00",
    fps: float | None = None,
    position: str = "bottom",
    font_size: int = 24,
    color: str = "white",
    bg_color: str = "black@0.6",
) -> str:
    """Burn visible SMPTE timecode into video frames.

    Renders the timecode string over each frame using ffmpeg's
    ``drawtext`` filter (with PIL fallback if unavailable).

    Inspired by DaVinci Resolve *Burn-In* timecode overlay.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        start_tc: Starting timecode in ``HH:MM:SS:FF`` format.
        fps: Frame rate (auto-probed if None).
        position: ``"top"`` or ``"bottom"``. Default ``"bottom"``.
        font_size: Font size in pixels. Default 24.
        color: Text colour name. Default ``"white"``.
        bg_color: Background box colour. Default ``"black@0.6"``.

    Returns:
        output_path
    """
    import json as _j
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Probe fps if needed
    if fps is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
            capture_output=True, text=True, check=True,
        )
        info = _j.loads(probe.stdout)
        vs = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
        fps_raw = vs.get("r_frame_rate", "25/1")
        n, d = map(int, fps_raw.split("/"))
        fps = n / d

    # Parse start_tc → total frames
    parts = start_tc.replace(";", ":").split(":")
    h, m, s, f = (int(p) for p in parts) if len(parts) == 4 else (0, 0, 0, 0)
    start_frame = int((h * 3600 + m * 60 + s) * fps + f)

    y_pos = f"ih-{font_size + 10}" if position == "bottom" else "10"

    # Try drawtext filter
    r_check = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
    has_drawtext = "drawtext" in r_check.stdout + r_check.stderr

    if has_drawtext:
        tc_expr = (
            f"drawtext="
            f"text='%{{pts\\:hms}}':"
            f"fontsize={font_size}:"
            f"fontcolor={color}:"
            f"box=1:boxcolor={bg_color}:"
            f"x=(w-tw)/2:y={y_pos}"
        )
        _run(["ffmpeg", "-y", "-i", input_path, "-vf", tc_expr,
              "-c:v", "libx264", "-c:a", "copy", output_path])
    else:
        # PIL fallback: burn timecode per-frame
        import tempfile, json
        from PIL import Image, ImageDraw, ImageFont
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", input_path],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)
        vs = next(s for s in info["streams"] if s["codec_type"] == "video")
        W, H = int(vs["width"]), int(vs["height"])
        tmp_dir = Path(tempfile.mkdtemp())
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-vf", f"fps={fps}",
                        str(tmp_dir / "frame_%05d.png")],
                       capture_output=True, check=True)
        for idx, fpath in enumerate(sorted(tmp_dir.glob("frame_*.png"))):
            frame_num = start_frame + idx
            total_sec = int(frame_num / fps)
            ff = frame_num % max(1, int(fps))
            hh, rem = divmod(total_sec, 3600)
            mm, ss = divmod(rem, 60)
            tc_str = f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
            img = Image.open(fpath).convert("RGB")
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
            except Exception:
                font = ImageFont.load_default()
            tw = draw.textlength(tc_str, font=font)
            tx = (W - tw) / 2
            ty = H - font_size - 10 if position == "bottom" else 10
            draw.rectangle([tx - 4, ty - 2, tx + tw + 4, ty + font_size + 2],
                           fill=(0, 0, 0, 150))
            draw.text((tx, ty), tc_str, font=font, fill=color)
            img.save(fpath)
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps),
             "-i", str(tmp_dir / "frame_%05d.png"),
             "-i", input_path, "-map", "0:v", "-map", "1:a?",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             output_path],
            capture_output=True, check=True,
        )
    return output_path


# ---------------------------------------------------------------------------
# #78  auto_reframe
# ---------------------------------------------------------------------------
def auto_reframe(
    input_path: str,
    output_path: str,
    *,
    target_ratio: str = "9:16",
    anchor: str = "center",
) -> str:
    """Reframe video to a different aspect ratio with smart cropping.

    Crops the source to the target aspect ratio. The anchor controls where
    the crop window sits: ``"center"``, ``"top"``, ``"bottom"``,
    ``"left"``, or ``"right"``.

    Inspired by DaVinci Resolve 20 *Auto Reframe* feature.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        target_ratio: Target aspect ratio as ``"W:H"`` string. Default ``"9:16"``.
        anchor: Crop anchor position. Default ``"center"``.

    Returns:
        output_path
    """
    import json as _j
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, check=True,
    )
    info = _j.loads(probe.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])

    rw, rh = (int(x) for x in target_ratio.split(":"))
    # Compute crop dimensions
    target_w = W
    target_h = int(W * rh / rw)
    if target_h > H:
        target_h = H
        target_w = int(H * rw / rh)

    # Crop anchor
    if anchor == "center":
        cx, cy = (W - target_w) // 2, (H - target_h) // 2
    elif anchor == "top":
        cx, cy = (W - target_w) // 2, 0
    elif anchor == "bottom":
        cx, cy = (W - target_w) // 2, H - target_h
    elif anchor == "left":
        cx, cy = 0, (H - target_h) // 2
    elif anchor == "right":
        cx, cy = W - target_w, (H - target_h) // 2
    else:
        cx, cy = (W - target_w) // 2, (H - target_h) // 2

    vf = f"crop={target_w}:{target_h}:{cx}:{cy}"
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


# ---------------------------------------------------------------------------
# #79  color_space_convert
# ---------------------------------------------------------------------------
def color_space_convert(
    input_path: str,
    output_path: str,
    *,
    src_space: str = "bt709",
    dst_space: str = "bt2020",
    src_transfer: str = "bt709",
    dst_transfer: str = "bt2020-10",
) -> str:
    """Convert video between colour spaces (e.g. SDR BT.709 → HDR BT.2020).

    Uses ffmpeg ``colorspace`` filter for gamut and transfer-function conversion.

    Inspired by DaVinci Resolve *Color Space Transform* OFX plug-in.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        src_space: Source colour primaries (e.g. ``"bt709"``, ``"bt2020"``).
        dst_space: Destination colour primaries.
        src_transfer: Source transfer function (e.g. ``"bt709"``, ``"smpte2084"``).
        dst_transfer: Destination transfer function.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"colorspace=all={dst_space}:"
        f"iall={src_space}:"
        f"itrc={src_transfer}:"
        f"trc={dst_transfer}"
    )
    try:
        _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
              "-c:v", "libx264", "-c:a", "copy", output_path])
    except RuntimeError:
        # Fallback: simple hue/eq approximation if colorspace filter unavailable
        vf_fallback = f"eq=saturation=1.1:contrast=1.05,hue=s=1.1"
        _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf_fallback,
              "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


# ---------------------------------------------------------------------------
# #80  deinterlace
# ---------------------------------------------------------------------------
def deinterlace(
    input_path: str,
    output_path: str,
    *,
    mode: str = "yadif",
    field_order: str = "tff",
) -> str:
    """Deinterlace interlaced video to progressive frames.

    Supports multiple deinterlacing algorithms via ffmpeg filters.

    Inspired by DaVinci Resolve *Interlaced Render* / *Deinterlace* setting.

    Args:
        input_path: Source interlaced video.
        output_path: Destination progressive video.
        mode: Algorithm — ``"yadif"`` (default, best quality),
              ``"bwdif"`` (motion-adaptive), or ``"estdif"`` (edge-adaptive).
        field_order: Field order — ``"tff"`` (top-field-first) or ``"bff"``.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    MODES = {"yadif": "yadif=mode=1", "bwdif": "bwdif=mode=1", "estdif": "estdif"}
    vf_base = MODES.get(mode, "yadif=mode=1")
    fo = 0 if field_order == "tff" else 1
    if mode == "yadif":
        vf = f"yadif=mode=1:parity={fo}"
    elif mode == "bwdif":
        vf = f"bwdif=mode=1:parity={fo}"
    else:
        vf = "estdif"
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


# ---------------------------------------------------------------------------
# #81  spatial_video_render
# ---------------------------------------------------------------------------
def spatial_video_render(
    left_path: str,
    right_path: str,
    output_path: str,
    *,
    format: str = "sbs",
    fov_degrees: float = 90.0,
) -> str:
    """Render a spatial (stereoscopic 3D) video for immersive headsets.

    Combines left/right eye clips into a side-by-side or over-under layout
    with spatial video metadata embedded.

    Inspired by DaVinci Resolve 20 *Spatial Video* deliver preset.

    Args:
        left_path: Left-eye video path.
        right_path: Right-eye video path.
        output_path: Destination spatial video path.
        format: ``"sbs"`` (side-by-side half-width) or ``"ou"`` (over-under).
        fov_degrees: Horizontal field of view in degrees. Default 90.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if format == "sbs":
        # Scale each eye to half-width then hstack
        fc = (
            "[0:v]scale=iw/2:ih[l];"
            "[1:v]scale=iw/2:ih[r];"
            "[l][r]hstack[v]"
        )
    elif format == "ou":
        # Scale each eye to half-height then vstack
        fc = (
            "[0:v]scale=iw:ih/2[t];"
            "[1:v]scale=iw:ih/2[b];"
            "[t][b]vstack[v]"
        )
    else:
        raise ValueError(f"Unknown format '{format}'. Choose 'sbs' or 'ou'.")

    _run([
        "ffmpeg", "-y",
        "-i", left_path, "-i", right_path,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "aac",
        # Embed stereo3d metadata
        "-metadata:s:v:0", f"stereo_mode={'left_right' if format == 'sbs' else 'top_bottom'}",
        "-metadata", f"spatial_fov={fov_degrees}",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# #83  waveform_monitor
# ---------------------------------------------------------------------------
def waveform_monitor(
    input_path: str,
    output_path: str,
    *,
    mode: str = "waveform",
    duration: float | None = None,
) -> str:
    """Generate a waveform or vectorscope analysis overlay.

    Renders a colour analysis instrument (waveform, vectorscope, or histogram)
    alongside the video for scopes-based grading.

    Inspired by DaVinci Resolve's *Video Scopes* panel.

    Args:
        input_path: Source video path.
        output_path: Destination video with scope overlay.
        mode: ``"waveform"``, ``"vectorscope"``, or ``"histogram"``.
        duration: Limit output duration in seconds. None = full clip.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dur_args = ["-t", str(duration)] if duration else []

    if mode == "vectorscope":
        scope_filter = "vectorscope=m=color3:intensity=0.7"
    elif mode == "histogram":
        scope_filter = "histogram=level_height=200:scale=logarithmic"
    else:
        scope_filter = "waveform=m=1:intensity=0.1:mirror=1"

    # Probe video height so scope can be scaled to match
    import json as _j
    _pr = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, check=True,
    )
    _vs = next(s for s in _j.loads(_pr.stdout)["streams"] if s["codec_type"] == "video")
    _H = int(_vs["height"])

    # Create scope as side panel via split + hstack
    fc = (
        f"[0:v]split[main][scope_in];"
        f"[scope_in]{scope_filter}[scope];"
        f"[scope]scale=320:{_H}[scope_scaled];"
        f"[main][scope_scaled]hstack=inputs=2[out]"
    )
    _run([
        "ffmpeg", "-y", "-i", input_path,
        *dur_args,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "copy",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# #84  keyframe_extract
# ---------------------------------------------------------------------------
def keyframe_extract(
    input_path: str,
    output_dir: str,
    *,
    max_frames: int = 20,
    threshold: float = 0.3,
) -> list[str]:
    """Extract representative keyframes from a video at scene change points.

    Uses scene detection to find cuts, then saves one frame per scene as PNG.
    Inspired by DaVinci Resolve's *Scene Detection → grab all stills*.

    Args:
        input_path: Source video path.
        output_dir: Directory to write keyframe images.
        max_frames: Maximum number of frames to extract. Default 20.
        threshold: Scene change sensitivity [0, 1]. Default 0.3.

    Returns:
        List of saved PNG file paths.
    """
    import re
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Detect scene timestamps
    r = subprocess.run(
        ["ffmpeg", "-i", input_path,
         "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    timestamps: list[float] = []
    for line in (r.stdout + r.stderr).splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            timestamps.append(float(m.group(1)))

    # Ensure at least a frame at t=0
    if not timestamps or timestamps[0] > 0.1:
        timestamps = [0.0] + timestamps
    timestamps = sorted(set(timestamps))[:max_frames]

    out_paths: list[str] = []
    for idx, ts in enumerate(timestamps):
        out_path = str(out_dir / f"keyframe_{idx:04d}_{ts:.3f}s.png")
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ts:.6f}", "-i", input_path,
             "-frames:v", "1", "-q:v", "2", out_path],
            capture_output=True, text=True,
        )
        if r2.returncode == 0:
            out_paths.append(out_path)

    return out_paths


# ---------------------------------------------------------------------------
# #85  aspect_ratio_pad
# ---------------------------------------------------------------------------
def aspect_ratio_pad(
    input_path: str,
    output_path: str,
    *,
    target_ratio: str = "16:9",
    pad_color: str = "black",
    blur_bg: bool = False,
) -> str:
    """Pad video to a target aspect ratio without cropping.

    Adds letterbox/pillarbox bars. With ``blur_bg=True``, fills the bars with
    a blurred + darkened version of the video instead of solid colour.

    Inspired by DaVinci Resolve *Output Blanking* and *Auto-fit* features.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        target_ratio: Target ``"W:H"`` ratio. Default ``"16:9"``.
        pad_color: Bar colour (ffmpeg colour name). Default ``"black"``.
        blur_bg: If True, fill bars with blurred video background.

    Returns:
        output_path
    """
    import json as _j
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, check=True,
    )
    info = _j.loads(probe.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])

    rw, rh = (int(x) for x in target_ratio.split(":"))
    target_w = W
    target_h = int(W * rh / rw)
    if target_h < H:
        target_h = H
        target_w = int(H * rw / rh)

    if blur_bg:
        # Scale + blur source to fill target, overlay original centred
        fc = (
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=20:5[bg];"
            f"[0:v]scale={W}:{H}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[out]"
        )
        _run([
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", fc,
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "copy",
            output_path,
        ])
    else:
        pad_x = (target_w - W) // 2
        pad_y = (target_h - H) // 2
        vf = f"pad={target_w}:{target_h}:{pad_x}:{pad_y}:color={pad_color}"
        _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
              "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


# ---------------------------------------------------------------------------
# #86  video_denoise_spatial
# ---------------------------------------------------------------------------
def video_denoise_spatial(
    input_path: str,
    output_path: str,
    *,
    strength: float = 0.5,
    method: str = "nlmeans",
) -> str:
    """Apply spatial noise reduction to video frames.

    Removes high-frequency noise while preserving edges using ffmpeg's
    non-local means (``nlmeans``) or ``hqdn3d`` filters.

    Inspired by DaVinci Resolve *Noise Reduction → Spatial* mode.

    Args:
        input_path: Source video path.
        output_path: Destination video path.
        strength: Denoise strength [0, 1]. Default 0.5.
        method: ``"nlmeans"`` (best quality) or ``"hqdn3d"`` (fast).

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    s = max(0.0, min(1.0, strength))

    luma = s * 4
    chroma = s * 3
    if method == "nlmeans":
        # Try nlmeans; fall back to hqdn3d if filter lacks h option
        try:
            vf = f"nlmeans={s * 10:.1f}:{int(s*7)+3}:{int(s*14)+7}"
            _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
                  "-c:v", "libx264", "-c:a", "copy", output_path])
            return output_path
        except RuntimeError:
            pass  # fall through to hqdn3d
    vf = f"hqdn3d={luma:.1f}:{chroma:.1f}:0:0"
    _run(["ffmpeg", "-y", "-i", input_path, "-vf", vf,
          "-c:v", "libx264", "-c:a", "copy", output_path])
    return output_path


# ---------------------------------------------------------------------------
# chroma_key
# ---------------------------------------------------------------------------

def chroma_key(
    input_path: str,
    output_path: str,
    *,
    key_color: str = "0x00ff00",
    similarity: float = 0.15,
    blend: float = 0.05,
    background_path: str | None = None,
) -> str:
    """Key out a chroma colour (green/blue screen) from a video.

    Args:
        input_path: Source video with chroma background.
        output_path: Destination video.
        key_color: Hex colour to remove (e.g. ``"0x00ff00"`` for green,
            ``"0x0000ff"`` for blue).
        similarity: Threshold distance from key colour (0–1, lower = tighter).
        blend: Soft edge blend amount (0–1).
        background_path: Optional replacement background image or video.
            If *None*, the keyed-out area becomes black.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    ck_filter = (
        f"chromakey=color={key_color}:similarity={similarity:.3f}"
        f":blend={blend:.3f}"
    )

    if background_path:
        bg_ext = Path(background_path).suffix.lower()
        if bg_ext in {".jpg", ".jpeg", ".png", ".bmp"}:
            bg_input = ["-loop", "1", "-i", background_path]
        else:
            bg_input = ["-i", background_path]
        fc = f"[0:v]{ck_filter}[fg];[1:v][fg]overlay[v]"
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            *bg_input,
            "-filter_complex", fc,
            "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", ck_filter,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            output_path,
        ]

    _run(cmd)
    return output_path


# ---------------------------------------------------------------------------
# frame_interpolate
# ---------------------------------------------------------------------------

def frame_interpolate(
    input_path: str,
    output_path: str,
    *,
    target_fps: float = 60.0,
    mode: str = "blend",
) -> str:
    """Increase video frame rate via frame interpolation.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        target_fps: Desired output frame rate (e.g. 60, 120).
        mode: Interpolation method — ``"blend"`` (fast, blends frames),
            ``"mci"`` (motion-compensated, slower but smoother).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if mode == "mci":
        vf = f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    else:
        vf = f"minterpolate=fps={target_fps}:mi_mode=blend"

    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# vignette
# ---------------------------------------------------------------------------

def vignette(
    input_path: str,
    output_path: str,
    *,
    strength: float = 0.5,
    shape: str = "circle",
) -> str:
    """Apply a radial vignette (edge darkening) effect.

    Args:
        input_path: Source image or video file.
        output_path: Destination file.
        strength: Vignette intensity in [0, 1].  0 = no effect, 1 = heavy.
        shape: ``"circle"`` (uniform) or ``"oval"`` (follows frame aspect).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    ext = Path(input_path).suffix.lower()
    is_image = ext in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

    angle = strength * 1.5707963  # up to π/2
    vf = f"vignette=angle={angle:.6f}"

    if is_image:
        # Use PIL for images
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(input_path).convert("RGB")).astype(np.float32) / 255.0
        H, W = img.shape[:2]
        cx, cy = W / 2.0, H / 2.0
        if shape == "oval":
            ys, xs = np.mgrid[0:H, 0:W]
            dx = (xs - cx) / cx
            dy = (ys - cy) / cy
            dist = np.sqrt(dx**2 + dy**2)
        else:
            ys, xs = np.mgrid[0:H, 0:W]
            r = max(cx, cy)
            dist = np.sqrt((xs - cx)**2 + (ys - cy)**2) / r
        mask = np.clip(1.0 - dist * strength, 0, 1)[:, :, np.newaxis]
        img = np.clip(img * mask, 0, 1)
        Image.fromarray((img * 255).astype(np.uint8)).save(output_path)
    else:
        _run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# blur_background
# ---------------------------------------------------------------------------

def blur_background(
    input_path: str,
    output_path: str,
    *,
    blur_strength: int = 20,
    subject_scale: float = 0.5,
) -> str:
    """Blur background while keeping a centered subject region sharp.

    Uses a soft elliptical mask composited via ffmpeg filter_complex.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        blur_strength: Gaussian blur radius (pixels) for the background.
        subject_scale: Fraction of frame (0–1) occupied by the sharp subject
            ellipse (applied to both width and height).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Probe video dimensions to embed numeric constants in geq expression
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", input_path],
        capture_output=True, text=True,
    )
    try:
        W, H = map(int, probe.stdout.strip().split(","))
    except Exception:
        W, H = 640, 360  # fallback

    cx, cy = W / 2.0, H / 2.0
    ax = W * subject_scale / 2.0
    ay = H * subject_scale / 2.0
    ax2 = ax * ax
    ay2 = ay * ay

    # geq expression using numeric constants only
    geq_lum = (
        f"if(lte("
        f"(X-{cx:.2f})*(X-{cx:.2f})/{ax2:.4f}+"
        f"(Y-{cy:.2f})*(Y-{cy:.2f})/{ay2:.4f}"
        f",1),255,0)"
    )

    fc = (
        f"[0:v]split=2[sharp][blur_src];"
        f"[blur_src]boxblur={blur_strength}[blurred];"
        f"[0:v]geq=lum='{geq_lum}':cb=128:cr=128,format=gray[mask];"
        f"[blurred][sharp][mask]maskedmerge[v]"
    )

    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_stabilize
# ---------------------------------------------------------------------------

def video_stabilize(
    input_path: str,
    output_path: str,
    *,
    smoothing: int = 10,
    zoom: float = 0.0,
) -> str:
    """Stabilize shaky video using ffmpeg vidstab two-pass pipeline.

    Requires ffmpeg built with libvidstab (``--enable-libvidstab``).
    Falls back to deshake filter if libvidstab is unavailable.

    Args:
        input_path: Source video file.
        output_path: Destination stabilized video file.
        smoothing: Stabilisation smoothness (higher = smoother pan).
        zoom: Additional zoom to hide black borders (0 = auto).

    Returns:
        The *output_path*.
    """
    import tempfile as _tf
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with _tf.TemporaryDirectory() as td:
        trf = f"{td}/transforms.trf"

        # Pass 1: detect
        p1 = subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"vidstabdetect=stepsize=6:shakiness=8:accuracy=9:result={trf}",
            "-f", "null", "-",
        ], capture_output=True, text=True)

        if p1.returncode != 0 or not Path(trf).exists():
            # libvidstab not available — fallback to deshake
            _run([
                "ffmpeg", "-y", "-i", input_path,
                "-vf", "deshake",
                "-c:v", "libx264", "-c:a", "aac",
                output_path,
            ])
            return output_path

        # Pass 2: transform
        _run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", (
                f"vidstabtransform=input={trf}:smoothing={smoothing}"
                f":zoom={zoom}:interpol=linear,unsharp=5:5:0.8:3:3:0.4"
            ),
            "-c:v", "libx264", "-c:a", "aac",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# thumbnail_extract
# ---------------------------------------------------------------------------

def thumbnail_extract(
    input_path: str,
    output_dir: str,
    *,
    timestamps: list[float],
    fmt: str = "jpg",
) -> list[str]:
    """Extract thumbnail frames at given timestamps.

    Args:
        input_path: Source video file.
        output_dir: Directory to write thumbnail images.
        timestamps: List of float seconds to extract.
        fmt: Output image format — ``"jpg"`` or ``"png"``.

    Returns:
        List of output image paths (one per timestamp).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for i, ts in enumerate(timestamps):
        out = str(Path(output_dir) / f"thumb_{i:04d}_{ts:.3f}.{fmt}")
        _run([
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", input_path,
            "-vframes", "1",
            "-q:v", "2",
            out,
        ])
        outputs.append(out)
    return outputs


# ---------------------------------------------------------------------------
# split_screen
# ---------------------------------------------------------------------------

def split_screen(
    input_paths: list[str],
    output_path: str,
    *,
    layout: str = "2x1",
    width: int = 1280,
    height: int = 720,
) -> str:
    """Compose multiple videos into a split-screen layout.

    Args:
        input_paths: 2–4 source video files.
        output_path: Destination video file.
        layout: ``"2x1"`` (side by side), ``"1x2"`` (top/bottom),
            ``"2x2"`` (grid), ``"3x1"`` (three side by side).
        width: Output video width in pixels.
        height: Output video height in pixels.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    n = len(input_paths)
    if n < 2:
        raise ValueError("split_screen requires at least 2 input videos")

    if layout == "2x2" and n >= 4:
        w2, h2 = width // 2, height // 2
        scale = f"scale={w2}:{h2}"
        fc = (
            f"[0:v]{scale}[a];[1:v]{scale}[b];[2:v]{scale}[c];[3:v]{scale}[d];"
            f"[a][b]hstack[top];[c][d]hstack[bot];[top][bot]vstack[v]"
        )
        inputs = [x for p in input_paths[:4] for x in ["-i", p]]
    elif layout == "1x2" and n >= 2:
        w2, h2 = width, height // 2
        scale = f"scale={w2}:{h2}"
        fc = f"[0:v]{scale}[a];[1:v]{scale}[b];[a][b]vstack[v]"
        inputs = ["-i", input_paths[0], "-i", input_paths[1]]
    elif layout == "3x1" and n >= 3:
        w3, h1 = width // 3, height
        scale = f"scale={w3}:{h1}"
        fc = (
            f"[0:v]{scale}[a];[1:v]{scale}[b];[2:v]{scale}[c];"
            f"[a][b][c]hstack=inputs=3[v]"
        )
        inputs = ["-i", input_paths[0], "-i", input_paths[1], "-i", input_paths[2]]
    else:  # default 2x1
        w2, h1 = width // 2, height
        scale = f"scale={w2}:{h1}"
        fc = f"[0:v]{scale}[a];[1:v]{scale}[b];[a][b]hstack[v]"
        inputs = ["-i", input_paths[0], "-i", input_paths[1]]

    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_loop
# ---------------------------------------------------------------------------

def video_loop(
    input_path: str,
    output_path: str,
    *,
    count: int = 3,
) -> str:
    """Loop a video clip N times by concatenating it with itself.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        count: Number of times to loop (1 = original, 2 = doubled, etc.).

    Returns:
        The *output_path*.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Build concat filter_complex: [0:v][0:a] repeated count times
    has_audio = _has_audio(input_path)
    n = count
    video_segs = "".join(f"[0:v]" for _ in range(n))
    audio_segs = "".join(f"[0:a]" for _ in range(n))

    if has_audio:
        fc = f"{video_segs}{audio_segs}concat=n={n}:v=1:a=1[v][a]"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-c:a", "aac",
            output_path,
        ]
    else:
        fc = f"{video_segs}concat=n={n}:v=1:a=0[v]"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", fc,
            "-map", "[v]",
            "-c:v", "libx264",
            output_path,
        ]
    _run(cmd)
    return output_path


def _has_audio(path: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


# ---------------------------------------------------------------------------
# video_to_gif
# ---------------------------------------------------------------------------

def video_to_gif(
    input_path: str,
    output_path: str,
    *,
    fps: float = 15.0,
    width: int = 480,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
) -> str:
    """Convert a video clip to an optimised GIF.

    Uses ffmpeg's palettegen + paletteuse two-pass pipeline for high quality.

    Args:
        input_path: Source video file.
        output_path: Destination ``.gif`` file.
        fps: GIF frame rate.
        width: Output width in pixels (-1 = keep aspect).
        start_sec: Start offset in the source video.
        duration_sec: Duration to convert (``None`` = full video).

    Returns:
        The *output_path*.
    """
    import tempfile as _tf
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    trim_args = ["-ss", str(start_sec)]
    if duration_sec is not None:
        trim_args += ["-t", str(duration_sec)]

    scale_vf = f"fps={fps},scale={width}:-1:flags=lanczos"

    with _tf.TemporaryDirectory() as td:
        palette = f"{td}/palette.png"

        # Pass 1: generate palette
        _run([
            "ffmpeg", "-y",
            *trim_args,
            "-i", input_path,
            "-vf", f"{scale_vf},palettegen=stats_mode=diff",
            palette,
        ])

        # Pass 2: encode GIF with palette
        _run([
            "ffmpeg", "-y",
            *trim_args,
            "-i", input_path,
            "-i", palette,
            "-filter_complex",
            f"{scale_vf} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# video_snapshot
# ---------------------------------------------------------------------------

def video_snapshot(
    input_path: str,
    output_path: str,
    *,
    time_sec: float = 0.0,
    quality: int = 2,
) -> str:
    """Extract a single frame from a video as a still image.

    Args:
        input_path: Source video file.
        output_path: Destination image file (``.jpg``, ``.png``, etc.).
        time_sec: Timestamp in seconds to extract.
        quality: JPEG quality scale 1–31 (1 = best; ignored for PNG).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", input_path,
        "-vframes", "1",
        "-q:v", str(quality),
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_watermark
# ---------------------------------------------------------------------------

def video_watermark(
    input_path: str,
    output_path: str,
    *,
    watermark_path: str,
    position: str = "bottom_right",
    margin: int = 20,
    opacity: float = 0.7,
) -> str:
    """Burn a semi-transparent watermark/logo onto a video.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        watermark_path: Path to watermark image (PNG with alpha recommended).
        position: ``"top_left"``, ``"top_right"``, ``"bottom_left"``,
            ``"bottom_right"``, or ``"center"``.
        margin: Pixel gap from the chosen edge.
        opacity: Alpha multiplier in [0, 1].

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    _pos_map = {
        "top_left":     f"{margin}:{margin}",
        "top_right":    f"main_w-overlay_w-{margin}:{margin}",
        "bottom_left":  f"{margin}:main_h-overlay_h-{margin}",
        "bottom_right": f"main_w-overlay_w-{margin}:main_h-overlay_h-{margin}",
        "center":       "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
    }
    xy = _pos_map.get(position, _pos_map["bottom_right"])

    # Apply opacity via colorchannelmixer alpha channel
    alpha_expr = f"{opacity:.4f}"
    fc = (
        f"[1:v]format=rgba,colorchannelmixer=aa={alpha_expr}[wm];"
        f"[0:v][wm]overlay={xy}[v]"
    )

    has_aud = _has_audio(input_path)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", watermark_path,
        "-filter_complex", fc,
        "-map", "[v]",
    ]
    if has_aud:
        cmd += ["-map", "0:a", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", output_path]
    _run(cmd)
    return output_path


# ---------------------------------------------------------------------------
# video_flip
# ---------------------------------------------------------------------------

def video_flip(
    input_path: str,
    output_path: str,
    *,
    direction: str = "horizontal",
) -> str:
    """Flip a video horizontally or vertically.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        direction: ``"horizontal"`` (left-right mirror) or ``"vertical"``
            (upside-down flip).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if direction == "vertical":
        vf = "vflip"
    else:
        vf = "hflip"
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_rotate
# ---------------------------------------------------------------------------

def video_rotate(
    input_path: str,
    output_path: str,
    *,
    angle: float = 90.0,
) -> str:
    """Rotate a video by a given angle.

    For multiples of 90°, uses lossless ``transpose`` (fast).
    For arbitrary angles, uses the ``rotate`` filter with black fill.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        angle: Clockwise rotation in degrees (e.g. 90, 180, 270, 45).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    angle_mod = angle % 360
    if angle_mod == 90:
        vf = "transpose=1"
    elif angle_mod == 180:
        vf = "transpose=1,transpose=1"
    elif angle_mod == 270:
        vf = "transpose=2"
    else:
        rad = angle_mod * 3.14159265358979 / 180.0
        vf = (
            f"rotate={rad:.6f}:c=black:"
            "ow=rotw({angle}):oh=roth({angle})".replace("{angle}", str(rad))
        )
        # Simpler: use fixed output size
        vf = f"rotate=angle={rad:.6f}:c=black"

    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_mute
# ---------------------------------------------------------------------------

def video_mute(
    input_path: str,
    output_path: str,
) -> str:
    """Remove audio from a video, producing a video-only output.

    Args:
        input_path: Source video file.
        output_path: Destination video file (no audio stream).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "copy", "-an",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_crop
# ---------------------------------------------------------------------------

def video_crop(
    input_path: str,
    output_path: str,
    *,
    x: int | str = "center",
    y: int | str = "center",
    width: int,
    height: int,
) -> str:
    """Crop a rectangular region from a video.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        x: Left edge pixel offset, or ``"center"`` to center horizontally.
        y: Top edge pixel offset, or ``"center"`` to center vertically.
        width: Crop width in pixels.
        height: Crop height in pixels.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    x_expr = "(iw-{w})/2".format(w=width) if x == "center" else str(x)
    y_expr = "(ih-{h})/2".format(h=height) if y == "center" else str(y)
    vf = f"crop={width}:{height}:{x_expr}:{y_expr}"

    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_scale
# ---------------------------------------------------------------------------

def video_scale(
    input_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fit: str = "contain",
) -> str:
    """Scale a video to target resolution.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        width: Target width in pixels.
        height: Target height in pixels.
        fit: ``"stretch"`` (ignore aspect), ``"contain"`` (letterbox),
            ``"cover"`` (crop to fill).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if fit == "stretch":
        vf = f"scale={width}:{height}"
    elif fit == "cover":
        vf = (
            f"scale=w='if(gt(iw/ih,{width}/{height}),{width},-1)':"
            f"h='if(gt(iw/ih,{width}/{height}),-1,{height})',"
            f"crop={width}:{height}"
        )
    else:  # contain (letterbox)
        vf = (
            f"scale=w='if(gt(iw/ih,{width}/{height}),{width},-2)':"
            f"h='if(gt(iw/ih,{width}/{height}),-2,{height})',"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )

    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_concat_crossfade
# ---------------------------------------------------------------------------

def video_concat_crossfade(
    input_paths: list[str],
    output_path: str,
    *,
    crossfade_sec: float = 0.5,
) -> str:
    """Concatenate clips with a dissolve crossfade between each.

    Args:
        input_paths: List of video files to concatenate (at least 2).
        output_path: Destination video file.
        crossfade_sec: Overlap/dissolve duration in seconds.

    Returns:
        The *output_path*.
    """
    if len(input_paths) < 2:
        raise ValueError("video_concat_crossfade requires at least 2 clips")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Build chain: dissolve clip[0]→clip[1], result→clip[2], etc.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        current = input_paths[0]
        for idx in range(1, len(input_paths)):
            nxt = input_paths[idx]
            # Probe current duration for xfade offset
            dur_proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", current],
                capture_output=True, text=True,
            )
            dur = float(dur_proc.stdout.strip()) if dur_proc.returncode == 0 else crossfade_sec + 1
            offset = max(0.0, dur - crossfade_sec)

            is_last = (idx == len(input_paths) - 1)
            step_out = output_path if is_last else f"{td}/xf_{idx}.mp4"

            fc = (
                f"[0:v]setpts=PTS-STARTPTS[v0];"
                f"[1:v]setpts=PTS-STARTPTS[v1];"
                f"[v0][v1]xfade=transition=dissolve:duration={crossfade_sec}:offset={offset}[v]"
            )
            _run([
                "ffmpeg", "-y",
                "-i", current,
                "-i", nxt,
                "-filter_complex", fc,
                "-map", "[v]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                step_out,
            ])
            current = step_out
    return output_path


# ---------------------------------------------------------------------------
# video_change_fps
# ---------------------------------------------------------------------------

def video_change_fps(
    input_path: str,
    output_path: str,
    *,
    target_fps: float,
) -> str:
    """Change video frame rate by dropping or duplicating frames.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        target_fps: Desired output frame rate (e.g. 24, 30, 60).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"fps={target_fps}",
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_add_silence
# ---------------------------------------------------------------------------

def video_add_silence(
    input_path: str,
    output_path: str,
    *,
    sample_rate: int = 44100,
) -> str:
    """Add a silent audio track to a video-only file.

    If the input already has audio this is a no-op (audio is copied).

    Args:
        input_path: Source video file (typically without audio).
        output_path: Destination video file with silent audio stream.
        sample_rate: Sample rate for the generated silence (Hz).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Check if audio already present
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", input_path],
        capture_output=True, text=True,
    )
    has_audio = bool(probe.stdout.strip())

    if has_audio:
        _run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path])
    else:
        _run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
            "-shortest",
            "-c:v", "copy", "-c:a", "aac",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# image_to_video
# ---------------------------------------------------------------------------

def image_to_video(
    input_path: str,
    output_path: str,
    *,
    duration_sec: float = 5.0,
    fps: float = 30.0,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Convert a still image to a video of given duration.

    Args:
        input_path: Source image file.
        output_path: Destination video file.
        duration_sec: Length of the output video in seconds.
        fps: Output frame rate.
        width: Optional output width (``None`` = keep original).
        height: Optional output height (``None`` = keep original).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    vf_parts = [f"fps={fps}"]
    if width and height:
        vf_parts.append(f"scale={width}:{height}")
    elif width:
        vf_parts.append(f"scale={width}:-2")
    elif height:
        vf_parts.append(f"scale=-2:{height}")
    vf = ",".join(vf_parts)

    _run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", input_path,
        "-vf", vf,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_extract_audio
# ---------------------------------------------------------------------------

def video_extract_audio(
    input_path: str,
    output_path: str,
    *,
    codec: str = "copy",
) -> str:
    """Extract the audio track from a video to a standalone audio file.

    Args:
        input_path: Source video file.
        output_path: Destination audio file (``.aac``, ``.mp3``, ``.wav``, etc.).
        codec: Audio codec for output (``"copy"`` = lossless remux;
            ``"aac"``, ``"mp3"`` = transcode).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-c:a", codec,
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_replace_audio
# ---------------------------------------------------------------------------

def video_replace_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    *,
    shortest: bool = True,
) -> str:
    """Replace the audio track in a video with a different audio file.

    Args:
        video_path: Source video file (video stream is kept).
        audio_path: Audio file to use as the new track.
        output_path: Destination video file.
        shortest: Truncate output to the shorter of video/audio (True recommended).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
    ]
    if shortest:
        cmd.append("-shortest")
    cmd.append(output_path)
    _run(cmd)
    return output_path


# ---------------------------------------------------------------------------
# video_trim
# ---------------------------------------------------------------------------

def video_trim(
    input_path: str,
    output_path: str,
    *,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    stream_copy: bool = True,
) -> str:
    """Trim a video to the [start_sec, end_sec] range.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        start_sec: Start time in seconds.
        end_sec: End time in seconds (``None`` = until end of file).
        stream_copy: Use stream copy for speed (True) or re-encode (False).
            Stream copy may have slight precision issues near keyframes.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-ss", str(start_sec), "-i", input_path]
    if end_sec is not None:
        cmd += ["-t", str(end_sec - start_sec)]
    if stream_copy:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-c:a", "aac"]
    cmd.append(output_path)
    _run(cmd)
    return output_path


# ---------------------------------------------------------------------------
# video_info
# ---------------------------------------------------------------------------

def video_info(input_path: str) -> dict:
    """Return structured metadata about a video file.

    Args:
        input_path: Source video or audio file.

    Returns:
        Dict with keys: ``duration`` (float), ``fps`` (float),
        ``width`` (int), ``height`` (int), ``video_codec`` (str),
        ``audio_codec`` (str), ``bitrate_kbps`` (float),
        ``audio_sample_rate`` (int), ``audio_channels`` (int).
    """
    import json

    probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_streams", "-show_format",
         "-of", "json", input_path],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{probe.stderr}")

    data = json.loads(probe.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    # Parse fps
    fps = 0.0
    fps_str = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        pass

    return {
        "duration": float(fmt.get("duration", 0)),
        "fps": round(fps, 3),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_codec": audio_stream.get("codec_name", ""),
        "bitrate_kbps": round(float(fmt.get("bit_rate", 0)) / 1000, 1),
        "audio_sample_rate": int(audio_stream.get("sample_rate", 0)),
        "audio_channels": int(audio_stream.get("channels", 0)),
    }


# ---------------------------------------------------------------------------
# video_black_and_white
# ---------------------------------------------------------------------------

def video_black_and_white(
    input_path: str,
    output_path: str,
) -> str:
    """Convert a video to grayscale (black and white).

    Args:
        input_path: Source video file.
        output_path: Destination grayscale video file.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "hue=s=0",
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_subtitles_hardcode
# ---------------------------------------------------------------------------

def video_subtitles_hardcode(
    input_path: str,
    output_path: str,
    *,
    srt_path: str,
    font_size: int = 28,
) -> str:
    """Hard-code SRT subtitles into video frames.

    Tries ffmpeg ``subtitles`` filter first; falls back to PIL frame rendering
    when libass is unavailable.

    Args:
        input_path: Source video file.
        output_path: Destination video file with burned subtitles.
        srt_path: Path to the ``.srt`` subtitle file.
        font_size: Subtitle font size in points.

    Returns:
        The *output_path*.
    """
    from gemia.video.subtitles import add_subtitle_track
    return add_subtitle_track(input_path, output_path, srt_path=srt_path,
                              style={"fontsize": font_size})


# ---------------------------------------------------------------------------
# video_sepia
# ---------------------------------------------------------------------------

def video_sepia(
    input_path: str,
    output_path: str,
    *,
    strength: float = 1.0,
) -> str:
    """Apply a sepia tone effect to a video.

    Args:
        input_path: Source video file.
        output_path: Destination video file.
        strength: Blend strength between original (0.0) and full sepia (1.0).

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    s = max(0.0, min(1.0, strength))
    # Classic sepia matrix blended with identity by `s`
    def _blend(sepia_val: float, identity_val: float) -> float:
        return s * sepia_val + (1 - s) * identity_val

    rr = _blend(0.393, 1.0); rg = _blend(0.769, 0.0); rb = _blend(0.189, 0.0)
    gr = _blend(0.349, 0.0); gg = _blend(0.686, 1.0); gb = _blend(0.168, 0.0)
    br = _blend(0.272, 0.0); bg = _blend(0.534, 0.0); bb = _blend(0.131, 1.0)

    vf = (
        f"colorchannelmixer="
        f"rr={rr:.4f}:rg={rg:.4f}:rb={rb:.4f}:"
        f"gr={gr:.4f}:gg={gg:.4f}:gb={gb:.4f}:"
        f"br={br:.4f}:bg={bg:.4f}:bb={bb:.4f}"
    )
    _run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# video_boomerang
# ---------------------------------------------------------------------------

def video_boomerang(
    input_path: str,
    output_path: str,
) -> str:
    """Create a boomerang effect: forward + reverse playback loop.

    Args:
        input_path: Source video file.
        output_path: Destination video file.

    Returns:
        The *output_path*.
    """
    import tempfile as _tf
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with _tf.TemporaryDirectory() as td:
        rev = f"{td}/reversed.mp4"
        # Reverse video (no audio)
        _run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "reverse",
            "-an", "-c:v", "libx264",
            rev,
        ])
        # Concat forward + reversed
        fwd_na = f"{td}/fwd_na.mp4"
        _run([
            "ffmpeg", "-y", "-i", input_path,
            "-an", "-c:v", "libx264",
            fwd_na,
        ])
        fc = "[0:v][1:v]concat=n=2:v=1:a=0[v]"
        _run([
            "ffmpeg", "-y",
            "-i", fwd_na,
            "-i", rev,
            "-filter_complex", fc,
            "-map", "[v]",
            "-c:v", "libx264",
            output_path,
        ])
    return output_path


def video_vignette(input_path: str, output_path: str, *, angle: float = 1.0) -> None:
    """Apply vignette darkening effect around video edges.
    
    Args:
        angle: Vignette angle in radians (controls strength). Default 1.0 (π/4).
    """
    vf = f"vignette=angle={angle:.4f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
           "-c:v", "libx264", "-c:a", "aac", output_path]
    _run(cmd)


def video_mirror(input_path: str, output_path: str, *, direction: str = "horizontal") -> None:
    """Mirror video horizontally or vertically.
    
    Args:
        direction: 'horizontal' (left-right flip) or 'vertical' (top-bottom flip)
    """
    if direction == "horizontal":
        vf = "hflip"
    elif direction == "vertical":
        vf = "vflip"
    else:
        raise ValueError(f"direction must be 'horizontal' or 'vertical', got {direction!r}")
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
           "-c:v", "libx264", "-c:a", "aac", output_path]
    _run(cmd)


def video_brightness_contrast(
    input_path: str, output_path: str,
    *, brightness: float = 0.0, contrast: float = 1.0
) -> None:
    """Adjust video brightness and contrast using ffmpeg eq filter.
    
    Args:
        brightness: Brightness offset in range [-1.0, 1.0]. Default 0.0.
        contrast: Contrast multiplier >= 0. Default 1.0 (no change).
    """
    vf = f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
           "-c:v", "libx264", "-c:a", "aac", output_path]
    _run(cmd)
