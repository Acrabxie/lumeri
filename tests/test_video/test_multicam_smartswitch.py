import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.multicam_smartswitch import render_ai_multicam_smartswitch_plan


def test_multicam_smartswitch_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.multicam_smartswitch.render_ai_multicam_smartswitch_plan" in catalog_for_prompt("video")


def test_multicam_smartswitch_writes_speaker_decisions_and_preview(tmp_path: Path) -> None:
    cam_a = tmp_path / "cam_a.mp4"
    cam_b = tmp_path / "cam_b.mp4"
    _make_dummy_video(cam_a, "testsrc=s=320x180:r=24:d=2.2", 440)
    _make_dummy_video(cam_b, "testsrc2=s=320x180:r=24:d=2.2", 660)
    output = tmp_path / "speaker_switch.mp4"

    result = render_ai_multicam_smartswitch_plan(
        [str(cam_a), str(cam_b)],
        str(output),
        speaker_segments=[
            {"speaker": "Alice", "start_seconds": 0.0, "end_seconds": 0.7},
            {"speaker": "Bob", "start_seconds": 0.7, "end_seconds": 1.4},
            {"speaker": "Alice", "start_seconds": 1.4, "end_seconds": 2.0},
        ],
        angle_labels=["Alice close", "Bob close"],
    )

    metadata = json.loads(output.with_suffix(".multicam_smartswitch.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert output.stat().st_size > 0
    assert metadata["effect"] == "resolve21_ai_multicam_smartswitch"
    assert metadata["strategy"] == "speaker_round_robin"
    assert metadata["switch_decisions"][0]["speaker"] == "Alice"
    assert metadata["switch_decisions"][1]["camera_index"] == 1
    assert metadata["switch_decisions"][2]["camera_index"] == 0
    assert any("diarization" in hint for hint in metadata["review_hints"])


def test_multicam_smartswitch_round_robin_fallback(tmp_path: Path) -> None:
    cam_a = tmp_path / "cam_a.mp4"
    cam_b = tmp_path / "cam_b.mp4"
    _make_dummy_video(cam_a, "testsrc=s=240x160:r=20:d=1.8", 330)
    _make_dummy_video(cam_b, "testsrc2=s=240x160:r=20:d=1.8", 550)
    output = tmp_path / "round_robin.mp4"

    render_ai_multicam_smartswitch_plan(
        [str(cam_a), str(cam_b)],
        str(output),
        clip_duration_seconds=0.6,
        strategy="round_robin",
    )

    metadata = json.loads(output.with_suffix(".multicam_smartswitch.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert metadata["strategy"] == "round_robin"
    assert [item["camera_index"] for item in metadata["switch_decisions"][:3]] == [0, 1, 0]
    assert metadata["diagnostics"] == []


def test_multicam_smartswitch_rejects_invalid_inputs(tmp_path: Path) -> None:
    cam = tmp_path / "cam.mp4"
    _make_dummy_video(cam, "testsrc=s=160x120:r=12:d=1", 440)
    with pytest.raises(ValueError, match="at least two"):
        render_ai_multicam_smartswitch_plan([str(cam)], str(tmp_path / "out.mp4"))
    with pytest.raises(FileNotFoundError):
        render_ai_multicam_smartswitch_plan([str(cam), str(tmp_path / "missing.mp4")], str(tmp_path / "out.mp4"))
    with pytest.raises(ValueError, match="clip_duration_seconds"):
        render_ai_multicam_smartswitch_plan([str(cam), str(cam)], str(tmp_path / "out.mp4"), clip_duration_seconds=0)


def test_multicam_smartswitch_with_real_local_angles(tmp_path: Path) -> None:
    real_video_paths = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_video_paths):
        pytest.skip("No two local real input videos found for multicam testing")

    output = tmp_path / "real_multicam.mp4"
    render_ai_multicam_smartswitch_plan(
        [str(path) for path in real_video_paths],
        str(output),
        speaker_segments=[
            {"speaker": "Host", "start_seconds": 0.0, "end_seconds": 0.45},
            {"speaker": "Guest", "start_seconds": 0.45, "end_seconds": 0.9},
            {"speaker": "Host", "start_seconds": 0.9, "end_seconds": 1.25},
        ],
        angle_labels=["host angle", "guest angle"],
    )

    metadata = json.loads(output.with_suffix(".multicam_smartswitch.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert output.stat().st_size > 0
    assert metadata["switch_decisions"][0]["angle_label"] == "host angle"
    assert metadata["switch_decisions"][1]["angle_label"] == "guest angle"


def _make_dummy_video(path: Path, video_filter: str, frequency: int) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", video_filter,
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2.5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
