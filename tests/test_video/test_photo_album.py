
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.photo_album import render_photo_album_lightbox_tether_ingest


def test_photo_album_lightbox_tether_is_planner_visible() -> None:
    clear_catalog_cache()
    catalog = catalog_for_prompt("video")
    assert "gemia.video.photo_album.render_photo_album_lightbox_tether_ingest" in catalog


def test_photo_album_lightbox_tether_writes_review_manifest(tmp_path: Path) -> None:
    inputs = _make_stills(tmp_path / "inputs", count=4)
    manifest_path = Path(render_photo_album_lightbox_tether_ingest(
        [str(path) for path in inputs],
        str(tmp_path / "album"),
        album_name="Canon selects",
        album_tags=["Selects", " Client Review "],
        ratings_by_name={inputs[0].name: 5, inputs[1].stem: 4},
        tether_session={"session_id": "sony-a7-001", "camera_model": "Sony A7 IV"},
        lightbox_columns=2,
    ))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_photo_album_lightbox_tether_ingest"
    assert manifest["album"]["slug"] == "canon-selects"
    assert manifest["album"]["asset_count"] == 4
    assert manifest["album"]["rating_summary"]["5"] == 1
    assert manifest["album"]["rating_summary"]["4"] == 1
    assert manifest["lightbox"]["columns"] == 2
    assert Path(manifest["lightbox"]["path"]).exists()
    assert manifest["tether_session"]["session_id"] == "sony-a7-001"
    assert manifest["assets"][0]["capture"]["camera_model"] == "Sony A7 IV"
    assert "client-review" in manifest["assets"][0]["tags"]
    assert manifest["sequence"]["frame_count"] == 4
    assert manifest["sequence"]["consistent_dimensions"] is True


def test_photo_album_lightbox_tether_validation(tmp_path: Path) -> None:
    image = _make_stills(tmp_path / "inputs", count=1)[0]
    with pytest.raises(ValueError, match="image_paths cannot be empty"):
        render_photo_album_lightbox_tether_ingest([], str(tmp_path / "out"))
    with pytest.raises(ValueError, match="lightbox_columns"):
        render_photo_album_lightbox_tether_ingest([str(image)], str(tmp_path / "out"), lightbox_columns=0)
    with pytest.raises(ValueError, match="ratings"):
        render_photo_album_lightbox_tether_ingest([str(image)], str(tmp_path / "out"), default_rating=9)


def test_photo_album_lightbox_tether_reproduces_with_demo_video_frames(tmp_path: Path) -> None:
    frames = _extract_real_frames(Path("inputs/demo.mp4"), tmp_path / "demo_frames")
    manifest = _run_real_repro(frames, tmp_path / "demo_album")
    assert manifest["album"]["asset_count"] == 2
    assert manifest["assets"][0]["width"] > 0
    assert manifest["assets"][0]["fingerprint"]


def test_photo_album_lightbox_tether_reproduces_with_timeline_video_frames(tmp_path: Path) -> None:
    frames = _extract_real_frames(Path("inputs/gemia_timeline_demo.mp4"), tmp_path / "timeline_frames")
    manifest = _run_real_repro(frames, tmp_path / "timeline_album")
    assert manifest["lightbox"]["rows"] == 1
    assert manifest["tether_session"]["capture_count"] == 2


def _run_real_repro(frames: list[Path], output_dir: Path) -> dict:
    manifest_path = Path(render_photo_album_lightbox_tether_ingest(
        [str(path) for path in frames],
        str(output_dir),
        album_name="real frame tether ingest",
        default_rating=4,
        lightbox_columns=2,
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert Path(manifest["lightbox"]["path"]).exists()
    assert all(asset["rating"] == 4 for asset in manifest["assets"])
    return manifest


def _make_stills(directory: Path, *, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        image = np.zeros((64, 96, 3), dtype=np.uint8)
        image[:, :, 0] = 30 + index * 10
        image[:, :, 1] = 70 + index * 12
        image[:, :, 2] = 110 + index * 8
        path = directory / f"still_{index}.png"
        assert cv2.imwrite(str(path), image)
        paths.append(path)
    return paths


def _extract_real_frames(video_path: Path, output_dir: Path) -> list[Path]:
    if not video_path.exists():
        pytest.skip(f"real local video not found: {video_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamps = [0.1, 0.4]
    frames = []
    for index, timestamp in enumerate(timestamps):
        frame = output_dir / f"{video_path.stem}_{index}.png"
        proc = subprocess.run([
            "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(frame),
        ], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert frame.exists()
        frames.append(frame)
    return frames
