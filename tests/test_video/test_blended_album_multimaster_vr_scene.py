from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.blended_album_multimaster_vr_scene import render_blended_album_multimaster_vr_scene


def test_blended_album_multimaster_vr_scene_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.blended_album_multimaster_vr_scene.render_blended_album_multimaster_vr_scene" in catalog_for_prompt("video")


def test_blended_album_multimaster_vr_scene_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="video_paths cannot be empty"):
        render_blended_album_multimaster_vr_scene([], str(tmp_path / "out.mp4"))
    with pytest.raises(FileNotFoundError, match="Video file not found"):
        render_blended_album_multimaster_vr_scene([str(tmp_path / "missing.mp4")], str(tmp_path / "out.mp4"))
    source = _make_video(tmp_path / "source.mp4", duration=0.7)
    with pytest.raises(FileNotFoundError, match="Still image file not found"):
        render_blended_album_multimaster_vr_scene([str(source)], str(tmp_path / "out.mp4"), still_image_paths=[str(tmp_path / "missing.png")])
    with pytest.raises(ValueError, match="max_seconds"):
        render_blended_album_multimaster_vr_scene([str(source)], str(tmp_path / "out.mp4"), max_seconds=0)


def test_blended_album_multimaster_vr_scene_writes_all_components(tmp_path: Path) -> None:
    clips = [_make_video(tmp_path / "hero_a.mp4", duration=1.0), _make_video(tmp_path / "hero_b.mp4", duration=1.1)]
    output = tmp_path / "scene.mp4"
    result = render_blended_album_multimaster_vr_scene([str(path) for path in clips], str(output), scene_id="unit_scene", max_seconds=0.5)
    metadata = json.loads(output.with_suffix(".blended_album_multimaster_vr.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_blended_album_multimaster_vr_scene"
    assert metadata["scene"]["still_count"] == 4
    assert metadata["continuity"]["trim_pass_count"] == 2
    assert metadata["continuity"]["group_count"] == 2
    assert metadata["continuity"]["vr_deliverable_count"] == 2
    for key in ("album_manifest", "album_contact_sheet", "multimaster_manifest", "layer_graph_manifest", "group_versions_manifest", "immersive_vr_manifest"):
        assert Path(metadata["components"][key]).exists(), key
    assert _probe_stream_count(output, "v") >= 1


def test_blended_album_multimaster_vr_scene_accepts_provided_stills(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "source.mp4", duration=0.8)
    stills = [_make_image(tmp_path / "still_a.png"), _make_image(tmp_path / "still_b.png")]
    output = tmp_path / "with_stills.mp4"
    render_blended_album_multimaster_vr_scene([str(source)], str(output), still_image_paths=[str(path) for path in stills], max_seconds=0.4)
    metadata = json.loads(output.with_suffix(".blended_album_multimaster_vr.json").read_text(encoding="utf-8"))
    assert metadata["scene"]["generated_still_paths"] == []
    assert metadata["scene"]["still_count"] == 2
    assert metadata["continuity"]["album_asset_count"] == 2


def test_blended_album_multimaster_vr_scene_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for blended album scene testing")
    for index, source in enumerate(real_inputs):
        output = tmp_path / f"real_{index}.mp4"
        render_blended_album_multimaster_vr_scene([str(source)], str(output), scene_id=f"real_scene_{index}", max_seconds=0.5)
        metadata = json.loads(output.with_suffix(".blended_album_multimaster_vr.json").read_text(encoding="utf-8"))
        assert metadata["scene"]["still_count"] == 2
        assert Path(metadata["components"]["album_contact_sheet"]).exists()
        assert metadata["continuity"]["trim_pass_count"] == 2
        assert _probe_stream_count(output, "v") >= 1


def _make_video(path: Path, duration: float) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s=160x90:r=12:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=540:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path


def _make_image(path: Path) -> Path:
    image = np.zeros((64, 96, 3), dtype=np.uint8)
    image[:, :, 0] = 80
    image[:, :, 1] = 120
    image[:, :, 2] = 190
    assert cv2.imwrite(str(path), image)
    return path


def _probe_stream_count(path: Path, selector: str) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", selector, "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return len([line for line in proc.stdout.splitlines() if line.strip()])
