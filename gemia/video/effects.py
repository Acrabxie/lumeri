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
