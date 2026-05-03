from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.ingest_search_graphics_scene import render_blended_ingest_search_graphics_scene


def test_blended_ingest_search_graphics_scene_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.ingest_search_graphics_scene.render_blended_ingest_search_graphics_scene" in catalog_for_prompt("video")


def test_blended_ingest_search_graphics_scene_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="video_paths cannot be empty"):
        render_blended_ingest_search_graphics_scene([], str(tmp_path / "out.mp4"))
    with pytest.raises(FileNotFoundError, match="Video file not found"):
        render_blended_ingest_search_graphics_scene([str(tmp_path / "missing.mp4")], str(tmp_path / "out.mp4"))
    source = _make_video(tmp_path / "source.mp4", duration=0.5)
    with pytest.raises(FileNotFoundError, match="Still image file not found"):
        render_blended_ingest_search_graphics_scene([str(source)], str(tmp_path / "out.mp4"), still_image_paths=[str(tmp_path / "missing.png")])
    with pytest.raises(ValueError, match="max_seconds"):
        render_blended_ingest_search_graphics_scene([str(source)], str(tmp_path / "out.mp4"), max_seconds=0)


def test_blended_ingest_search_graphics_scene_writes_components(tmp_path: Path) -> None:
    dialogue = _make_video(tmp_path / "dialogue_source.mp4", duration=1.0)
    music = _make_video(tmp_path / "music_source.mp4", duration=1.1)
    output = tmp_path / "blended.mp4"
    result = render_blended_ingest_search_graphics_scene(
        [str(dialogue), str(music)],
        str(output),
        query="dialogue music",
        preset="scanline_caption",
        title="Unit ingest",
        max_seconds=0.7,
    )
    metadata = json.loads(output.with_suffix(".blended_ingest_search_graphics.json").read_text(encoding="utf-8"))
    components = metadata["components"]
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_blended_ingest_search_graphics_scene"
    assert metadata["still_count"] == 4
    assert metadata["search"]["match_count"] >= 1
    for key in (
        "photo_page_manifest",
        "photo_contact_sheet",
        "slate_video",
        "slate_metadata",
        "intellisearch_index",
        "intellisearch_search",
        "fairlight_folder_tracks",
        "graphics_metadata",
    ):
        assert components[key]
        assert Path(components[key]).exists(), key
    fairlight = json.loads(Path(components["fairlight_folder_tracks"]).read_text(encoding="utf-8"))
    assert fairlight["effect"] == "resolve21_fairlight_folder_tracks"
    assert _probe_stream_count(output, "v") >= 1
    assert _probe_stream_count(output, "a") >= 1


def test_blended_ingest_search_graphics_scene_accepts_provided_stills(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "source.mp4", duration=0.8)
    stills = [_make_image(tmp_path / "still_a.png"), _make_image(tmp_path / "still_b.png")]
    output = tmp_path / "with_stills.mp4"
    render_blended_ingest_search_graphics_scene([str(source)], str(output), still_image_paths=[str(path) for path in stills], max_seconds=0.5)
    metadata = json.loads(output.with_suffix(".blended_ingest_search_graphics.json").read_text(encoding="utf-8"))
    assert metadata["generated_still_paths"] == []
    assert metadata["still_count"] == 2


def test_blended_ingest_search_graphics_scene_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for blended ingest scene testing")
    for index, source in enumerate(real_inputs):
        output = tmp_path / f"real_{index}.mp4"
        render_blended_ingest_search_graphics_scene(
            [str(source)],
            str(output),
            query=source.stem.replace("_", " "),
            preset=("orbit_grid" if index == 0 else "radial_echo"),
            title=f"Real ingest {index}",
            max_seconds=0.8,
        )
        metadata = json.loads(output.with_suffix(".blended_ingest_search_graphics.json").read_text(encoding="utf-8"))
        assert metadata["effect"] == "resolve21_blended_ingest_search_graphics_scene"
        assert metadata["still_count"] == 2
        assert Path(metadata["components"]["photo_contact_sheet"]).exists()
        assert _probe_stream_count(output, "v") >= 1
        assert _probe_stream_count(output, "a") >= 1


def _make_video(path: Path, duration: float) -> Path:
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=160x90:r=12:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=550:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return path


def _make_image(path: Path) -> Path:
    image = np.zeros((64, 96, 3), dtype=np.uint8)
    image[:, :, 0] = 60
    image[:, :, 1] = 120
    image[:, :, 2] = 180
    assert cv2.imwrite(str(path), image)
    return path


def _probe_stream_count(path: Path, selector: str) -> int:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", selector, "-show_entries", "stream=index", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return len([line for line in proc.stdout.splitlines() if line.strip()])
