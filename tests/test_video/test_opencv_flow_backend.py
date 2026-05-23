from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.video.backends import (
    FlowTrackingAnalysisResult,
    OpenCVFlowTrackingBackend,
    render_opencv_flow_tracking_backend_manifest,
)


def test_opencv_flow_backend_exports() -> None:
    backend = OpenCVFlowTrackingBackend()
    assert backend.name == "github_opencv_flow_tracking_backend"
    assert FlowTrackingAnalysisResult.__name__ == "FlowTrackingAnalysisResult"


def test_opencv_flow_backend_analyzes_synthetic_motion(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "motion.mp4")
    result = OpenCVFlowTrackingBackend().analyze(
        clip,
        sample_stride=2,
        max_samples=8,
        max_sparse_points=24,
    )
    assert result.backend == "github_opencv_flow_tracking_backend"
    assert result.source_path == str(clip.resolve())
    assert result.frame_size == (160, 90)
    assert result.sampled_frame_count >= 2
    assert result.analyzed_pair_count >= 1
    assert result.mean_magnitude >= 0.0
    assert result.max_magnitude >= result.mean_magnitude
    assert 0.0 < result.confidence <= 1.0
    assert result.sample_summaries
    assert result.sample_summaries[0]["motion_vectors"]
    assert result.cache_key
    assert result.source_probe["media_kind"] == "video"
    assert result.to_dict()["backend"] == "github_opencv_flow_tracking_backend"


def test_opencv_flow_backend_rejects_bad_inputs(tmp_path: Path) -> None:
    backend = OpenCVFlowTrackingBackend()
    with pytest.raises(FileNotFoundError):
        backend.analyze(tmp_path / "missing.mp4")
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError, match="not a file"):
        backend.analyze(directory)
    audio = tmp_path / "audio.mp3"
    _make_audio(audio)
    with pytest.raises(ValueError, match="visual media"):
        backend.analyze(audio)


def test_opencv_flow_tracking_manifest_with_two_real_videos(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real videos available for OpenCV backend reproduction")
    manifest_path = Path(
        render_opencv_flow_tracking_backend_manifest(
            real_inputs,
            tmp_path / "opencv_flow_manifest",
            sample_stride=8,
            max_samples=6,
            max_sparse_points=16,
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "github_opencv_flow_tracking_backend"
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["sources"]) == 2
    assert len(manifest["analyses"]) == 2
    assert all(item["cache_key"] for item in manifest["sources"])
    assert all(item["analyzed_pair_count"] >= 1 for item in manifest["analyses"])
    assert all(0.0 <= item["confidence"] <= 1.0 for item in manifest["analyses"])
    assert "retime_signal" in manifest["interchange"]


def test_opencv_flow_tracking_manifest_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_opencv_flow_tracking_backend_manifest([], tmp_path)


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=24:d=1.2",
            "-vf", "drawbox=x='10+30*t':y=30:w=20:h=20:color=white:t=fill",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path


def _make_audio(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-c:a", "libmp3lame",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
