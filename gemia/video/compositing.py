"""Video compositing: overlay, add_audio_track."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {' '.join(cmd)}\nSTDERR:\n{proc.stderr}"
        )


def overlay(base_path: str, overlay_path: str, output_path: str, *,
            x: int = 0, y: int = 0, start_sec: float = 0.0,
            end_sec: float | None = None) -> str:
    """Overlay a video/image on top of a base video.

    Args:
        base_path: Background video.
        overlay_path: Foreground video or image.
        output_path: Destination.
        x: Horizontal offset of overlay.
        y: Vertical offset of overlay.
        start_sec: When the overlay appears (seconds).
        end_sec: When the overlay disappears.  ``None`` = until end.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    enable = f"between(t,{start_sec},{end_sec})" if end_sec else f"gte(t,{start_sec})"
    _run([
        "ffmpeg", "-y",
        "-i", base_path,
        "-i", overlay_path,
        "-filter_complex",
        f"[1:v]format=yuva420p[ovr];[0:v][ovr]overlay={x}:{y}:enable='{enable}'[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        output_path,
    ])
    return output_path


def add_audio_track(video_path: str, audio_path: str, output_path: str, *,
                    replace: bool = False, volume: float = 1.0) -> str:
    """Add an audio track to a video.

    Args:
        video_path: Input video.
        audio_path: Audio file to add (wav, mp3, aac, etc.).
        output_path: Destination.
        replace: If True, replace existing audio.  If False, mix with
            the original audio.
        volume: Volume multiplier for the new audio track.

    Returns:
        The *output_path*.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if replace:
        _run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-filter_complex", f"[1:a]volume={volume}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:a", "aac", "-shortest",
            output_path,
        ])
    else:
        _run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex",
            f"[1:a]volume={volume}[bg];[0:a][bg]amix=inputs=2:duration=first[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# object_remove
# ---------------------------------------------------------------------------
def object_remove(input_path: str, output_path: str, *, mask: str | None = None) -> str:
    """Remove objects from video using ffmpeg removelogo or blur inpainting.

    Note: Production-quality removal requires external tools (Runway, Adobe).
    This provides a best-effort ffmpeg approximation.

    Args:
        input_path: Source video.
        output_path: Destination video.
        mask: Path to binary mask image (white=remove, black=keep).
              If None, applies full-frame blur as placeholder.

    Returns:
        output_path
    """
    if mask:
        _run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", mask,
            "-lavfi", f"removelogo={mask}",
            "-c:a", "copy",
            output_path,
        ])
    else:
        _run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", "boxblur=10:1",
            "-c:a", "copy",
            output_path,
        ])
    return output_path


# ---------------------------------------------------------------------------
# background_replace
# ---------------------------------------------------------------------------
def background_replace(
    input_path: str,
    output_path: str,
    *,
    bg: str,
    method: str = "chroma",
) -> str:
    """Replace video background using chroma or luma key.

    Args:
        input_path: Foreground video (green/blue screen or white background).
        output_path: Destination video.
        bg: Path to replacement background image or video.
        method: ``"chroma"`` (green screen) or ``"luma"`` (white background).

    Returns:
        output_path
    """
    from pathlib import Path as _Path
    bg_ext = _Path(bg).suffix.lower()
    is_bg_image = bg_ext in {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

    if is_bg_image:
        bg_inputs = ["-loop", "1", "-i", bg]
        shortest = ["-shortest"]
    else:
        bg_inputs = ["-i", bg]
        shortest = []

    if method == "chroma":
        filtergraph = "[1:v][0:v]scale2ref[bg][fg];[fg]chromakey=0x00ff00:0.1:0.2[fgkey];[bg][fgkey]overlay"
    else:
        filtergraph = "[1:v][0:v]scale2ref[bg][fg];[fg]lumakey=0.0:0.1:0.1[fgkey];[bg][fgkey]overlay"

    _run([
        "ffmpeg", "-y",
        "-i", input_path,
        *bg_inputs,
        "-filter_complex", filtergraph,
        "-c:a", "copy",
        *shortest,
        output_path,
    ])
    return output_path
