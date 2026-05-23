from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from hashlib import md5

import pytest

from gemia.video.transitions import transition_custom, transition_shutter


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe is not available")


def _make_video(path: Path, *, size: str, rate: int, color: str) -> Path:
    _require_ffmpeg()
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r={rate}:d=1.2",
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return path


def _probe_video(path: Path) -> dict[str, str]:
    _require_ffmpeg()
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _read_frame(path: Path, *, seconds: float) -> "np.ndarray":
    cv2 = pytest.importorskip("cv2")
    cap = cv2.VideoCapture(str(path))
    try:
        assert cap.isOpened()
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
        ok, frame = cap.read()
        assert ok and frame is not None
        return frame
    finally:
        cap.release()


def test_custom_transition_normalizes_mismatched_video_streams(tmp_path: Path) -> None:
    first = _make_video(tmp_path / "wide-30fps.mp4", size="128x72", rate=30, color="red")
    second = _make_video(tmp_path / "small-24fps.mp4", size="96x54", rate=24, color="blue")
    output = tmp_path / "circle-transition.mp4"

    result = transition_custom(
        str(first),
        str(second),
        str(output),
        mask_fn="circle",
        duration_sec=0.3,
    )
    video = _probe_video(output)

    assert result == str(output)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert video["width"] == "128"
    assert video["height"] == "72"


def test_shutter_transition_outputs_black_aperture_closure(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    first = _make_video(tmp_path / "wide-30fps.mp4", size="96x54", rate=30, color="red")
    second = _make_video(tmp_path / "small-24fps.mp4", size="64x36", rate=24, color="blue")
    output = tmp_path / "shutter-transition.mp4"

    result = transition_shutter(
        str(first),
        str(second),
        str(output),
        duration_sec=0.4,
        blade_count=6,
    )
    video = _probe_video(output)
    mid = _read_frame(output, seconds=1.0)
    black_fraction = float(np.mean(np.max(mid, axis=2) < 18))

    assert result == str(output.resolve())
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert video["width"] == "96"
    assert video["height"] == "54"
    assert black_fraction > 0.75


def test_custom_transition_camera_shutter_alias_uses_local_shutter(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    first = _make_video(tmp_path / "first.mp4", size="80x44", rate=30, color="green")
    second = _make_video(tmp_path / "second.mp4", size="80x44", rate=30, color="blue")
    output = tmp_path / "custom-shutter.mp4"

    result = transition_custom(
        str(first),
        str(second),
        str(output),
        mask_fn="camera_shutter",
        duration_sec=0.4,
        hold_sec=0.05,
        edge_highlight=True,
    )
    mid = _read_frame(output, seconds=1.0)
    black_fraction = float(np.mean(np.max(mid, axis=2) < 18))

    assert result == str(output.resolve())
    assert black_fraction > 0.75


def test_shutter_feedback_args_change_render_and_hold_closed(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    first = _make_video(tmp_path / "first.mp4", size="96x54", rate=30, color="red")
    second = _make_video(tmp_path / "second.mp4", size="96x54", rate=30, color="blue")
    default_output = tmp_path / "default-shutter.mp4"
    feedback_output = tmp_path / "feedback-shutter.mp4"

    transition_shutter(
        str(first),
        str(second),
        str(default_output),
        duration_sec=0.4,
        blade_count=6,
    )
    result = transition_shutter(
        str(first),
        str(second),
        str(feedback_output),
        duration_sec=0.4,
        blade_count=6,
        hold_sec=0.1,
        edge_highlight=True,
    )

    assert result == str(feedback_output.resolve())
    assert md5(default_output.read_bytes()).hexdigest() != md5(feedback_output.read_bytes()).hexdigest()
    hold_frame = _read_frame(feedback_output, seconds=1.0)
    black_fraction = float(np.mean(np.max(hold_frame, axis=2) < 18))
    dark_pixels = hold_frame[np.max(hold_frame, axis=2) < 26]
    assert black_fraction > 0.75
    assert float(np.std(dark_pixels)) > 1.0
