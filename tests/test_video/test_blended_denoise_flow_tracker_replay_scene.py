from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.blended_denoise_flow_tracker_replay_scene import (
    render_blended_denoise_flow_tracker_replay_scene,
)


def test_blended_denoise_flow_tracker_replay_scene_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.blended_denoise_flow_tracker_replay_scene.render_blended_denoise_flow_tracker_replay_scene"
        in catalog_for_prompt("video")
    )


def test_blended_denoise_flow_tracker_replay_scene_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="video_paths cannot be empty"):
        render_blended_denoise_flow_tracker_replay_scene([], str(tmp_path / "out.mp4"))
    with pytest.raises(FileNotFoundError, match="Video file not found"):
        render_blended_denoise_flow_tracker_replay_scene([str(tmp_path / "missing.mp4")], str(tmp_path / "out.mp4"))
    source = _make_video(tmp_path / "source.mp4")
    with pytest.raises(ValueError, match="max_seconds"):
        render_blended_denoise_flow_tracker_replay_scene([str(source)], str(tmp_path / "out.mp4"), max_seconds=0)


def test_blended_denoise_flow_tracker_replay_scene_writes_all_components(tmp_path: Path) -> None:
    clips = [_make_video(tmp_path / "cam_a.mp4"), _make_video(tmp_path / "cam_b.mp4")]
    output = tmp_path / "scene.mp4"
    result = render_blended_denoise_flow_tracker_replay_scene(
        [str(path) for path in clips],
        str(output),
        scene_id="unit_scene",
        max_seconds=0.4,
    )
    metadata = json.loads(output.with_suffix(".blended_denoise_flow_tracker_replay.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_blended_denoise_flow_tracker_replay_scene"
    assert metadata["scene"]["clip_count"] == 2
    assert metadata["continuity"]["denoise_assignment_count"] == 2
    assert metadata["continuity"]["retime_assignment_count"] == 2
    assert metadata["continuity"]["track_assignment_count"] == 2
    assert metadata["continuity"]["fusion_assignment_count"] == 2
    assert metadata["continuity"]["replay_segment_count"] == 2
    assert len(metadata["continuity"]["shared_asset_refs"]) == 2
    for path in metadata["components"].values():
        assert Path(path).exists(), path
    assert _probe_stream_count(output, "v") >= 1


def test_blended_denoise_flow_tracker_replay_scene_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for blended denoise/flow/tracker/replay scene testing")
    pairs = [real_inputs, list(reversed(real_inputs))]
    for index, pair in enumerate(pairs, 1):
        output = tmp_path / f"real_{index}.mp4"
        render_blended_denoise_flow_tracker_replay_scene(
            [str(path) for path in pair],
            str(output),
            scene_id=f"real_scene_{index}",
            max_seconds=0.5,
        )
        metadata = json.loads(output.with_suffix(".blended_denoise_flow_tracker_replay.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert metadata["scene"]["clip_count"] == 2
        assert metadata["continuity"]["shared_asset_refs"]
        assert metadata["continuity"]["replay_segment_count"] == 2
        assert _probe_stream_count(output, "v") >= 1


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=860:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
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
