import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.photo_page import render_photo_page_batch_raw_grade


def test_photo_page_raw_grade_is_planner_visible() -> None:
    clear_catalog_cache()
    catalog = catalog_for_prompt("video")
    assert "gemia.video.photo_page.render_photo_page_batch_raw_grade" in catalog


def test_photo_page_raw_grade_writes_manifest_outputs_and_contact_sheet(tmp_path: Path) -> None:
    inputs = _make_image_set(tmp_path / "inputs", count=4)
    manifest_path = Path(
        render_photo_page_batch_raw_grade(
            [str(path) for path in inputs],
            str(tmp_path / "photo_batch"),
            preset="warm",
            exposure_stops=0.25,
            temperature_shift=8.0,
            contact_sheet_columns=2,
        )
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["effect"] == "resolve21_photo_page_batch_raw_grade"
    assert manifest["image_count"] == 4
    assert manifest["grade_settings"]["preset"] == "warm"
    assert manifest["contact_sheet"]["columns"] == 2
    assert Path(manifest["contact_sheet"]["path"]).exists()
    assert len(list((tmp_path / "photo_batch" / "graded").glob("*.png"))) == 4
    first = manifest["images"][0]
    assert first["width"] == 96
    assert first["height"] == 64
    assert first["input_mean_bgr"] != first["output_mean_bgr"]
    assert first["input_luma_mean"] != first["output_luma_mean"]


def test_photo_page_raw_grade_validation(tmp_path: Path) -> None:
    valid = _make_image_set(tmp_path / "inputs", count=1)[0]
    with pytest.raises(ValueError, match="image_paths cannot be empty"):
        render_photo_page_batch_raw_grade([], str(tmp_path / "out"))
    with pytest.raises(ValueError, match="contact_sheet_columns"):
        render_photo_page_batch_raw_grade([str(valid)], str(tmp_path / "out"), contact_sheet_columns=0)
    with pytest.raises(ValueError, match="Unsupported image extension"):
        render_photo_page_batch_raw_grade([str(tmp_path / "bad.txt")], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError, match="Image file not found"):
        render_photo_page_batch_raw_grade([str(tmp_path / "missing.png")], str(tmp_path / "out"))
    unreadable = tmp_path / "unreadable.png"
    unreadable.write_bytes(b"not a png")
    with pytest.raises(OSError, match="Could not read image file"):
        render_photo_page_batch_raw_grade([str(unreadable)], str(tmp_path / "out"))


def test_photo_page_raw_grade_reproduces_with_demo_video_frames(tmp_path: Path) -> None:
    frames = _extract_real_frames(Path("inputs/demo.mp4"), tmp_path / "demo_frames")
    manifest = _run_real_frame_repro(frames, tmp_path / "demo_batch", preset="cool")
    assert manifest["image_count"] == 2
    assert len(manifest["images"]) == 2


def test_photo_page_raw_grade_reproduces_with_timeline_video_frames(tmp_path: Path) -> None:
    frames = _extract_real_frames(Path("inputs/gemia_timeline_demo.mp4"), tmp_path / "timeline_frames")
    manifest = _run_real_frame_repro(frames, tmp_path / "timeline_batch", preset="cyberpunk")
    assert manifest["image_count"] == 2
    assert manifest["contact_sheet"]["rows"] == 1


def _run_real_frame_repro(frames: list[Path], output_dir: Path, *, preset: str) -> dict:
    manifest_path = Path(
        render_photo_page_batch_raw_grade(
            [str(path) for path in frames],
            str(output_dir),
            preset=preset,
            exposure_stops=0.15,
            temperature_shift=-6.0,
            contact_sheet_columns=2,
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert Path(manifest["contact_sheet"]["path"]).exists()
    for record in manifest["images"]:
        assert Path(record["output_path"]).exists()
        assert record["width"] > 0
        assert record["height"] > 0
    return manifest


def _make_image_set(directory: Path, *, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        image = np.zeros((64, 96, 3), dtype=np.uint8)
        image[:, :, 0] = 30 + index * 20
        image[:, :, 1] = 50 + index * 15
        image[:, :, 2] = 80 + index * 10
        path = directory / f"still_{index}.png"
        assert cv2.imwrite(str(path), image)
        paths.append(path)
    return paths


def _extract_real_frames(video_path: Path, output_dir: Path) -> list[Path]:
    if not video_path.exists():
        pytest.skip(f"real local video not found: {video_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = _probe_duration(video_path)
    timestamps = [max(duration * 0.25, 0.0), max(duration * 0.75, 0.05)]
    frames: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        frame = output_dir / f"{video_path.stem}_{index}.png"
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert frame.exists()
        frames.append(frame)
    return frames


def _probe_duration(video_path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return max(float(proc.stdout.strip()), 0.1)
