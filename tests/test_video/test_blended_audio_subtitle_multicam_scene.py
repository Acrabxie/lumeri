import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.blended_audio_subtitle_multicam_scene import render_blended_audio_subtitle_multicam_scene


def test_blended_scene_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.blended_audio_subtitle_multicam_scene.render_blended_audio_subtitle_multicam_scene"
        in catalog_for_prompt("video")
    )


def test_blended_scene_writes_pipeline_sidecar(tmp_path: Path) -> None:
    primary = tmp_path / "primary.mp4"
    secondary = tmp_path / "secondary.mp4"
    music = tmp_path / "music.m4a"
    _make_video(primary, "testsrc=s=320x180:r=20:d=1.8", 440)
    _make_video(secondary, "testsrc2=s=320x180:r=20:d=1.8", 660)
    _make_audio(music, duration=2.4)

    output = tmp_path / "blended.mp4"
    result = render_blended_audio_subtitle_multicam_scene(
        str(primary),
        str(secondary),
        str(music),
        str(output),
        transcript="Review the cut",
        target_duration_seconds=1.2,
    )

    metadata = json.loads(output.with_suffix(".blended_audio_subtitle_multicam.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_blended_audio_subtitle_multicam_scene"
    assert [step["name"] for step in metadata["pipeline_steps"]] == [
        "dialogue_matcher",
        "multicam_smartswitch",
        "animated_subtitles",
        "music_editor",
    ]
    assert all(step["output_exists"] for step in metadata["pipeline_steps"])
    assert all(step["metadata_exists"] for step in metadata["pipeline_steps"])
    assert len(metadata["speaker_segments"]) >= 2
    assert len(metadata["word_timings"]) == 3
    assert any("real footage" in hint for hint in metadata["review_hints"])


def test_blended_scene_rejects_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="primary video"):
        render_blended_audio_subtitle_multicam_scene(
            str(tmp_path / "missing-a.mp4"),
            str(tmp_path / "missing-b.mp4"),
            str(tmp_path / "missing.m4a"),
            str(tmp_path / "out.mp4"),
        )


def test_blended_scene_rejects_invalid_duration(tmp_path: Path) -> None:
    primary = tmp_path / "primary.mp4"
    secondary = tmp_path / "secondary.mp4"
    music = tmp_path / "music.m4a"
    _make_video(primary, "testsrc=s=160x90:r=12:d=1", 440)
    _make_video(secondary, "testsrc2=s=160x90:r=12:d=1", 660)
    _make_audio(music, duration=1.0)
    with pytest.raises(ValueError, match="target_duration_seconds"):
        render_blended_audio_subtitle_multicam_scene(
            str(primary), str(secondary), str(music), str(tmp_path / "out.mp4"), target_duration_seconds=0
        )


def test_blended_scene_with_two_real_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for blended scene testing")
    music = tmp_path / "generated_music.m4a"
    _make_audio(music, duration=4.0)

    pairs = [real_inputs, list(reversed(real_inputs))]
    for index, pair in enumerate(pairs, 1):
        output = tmp_path / f"real_blended_{index}.mp4"
        render_blended_audio_subtitle_multicam_scene(
            str(pair[0]),
            str(pair[1]),
            str(music),
            str(output),
            transcript="Review the multicam subtitle mix",
            target_duration_seconds=1.4,
        )
        metadata = json.loads(output.with_suffix(".blended_audio_subtitle_multicam.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert output.stat().st_size > 0
        assert metadata["source_paths"]["primary_video"] == str(pair[0].resolve())
        assert metadata["pipeline_steps"][-1]["name"] == "music_editor"


def _make_video(path: Path, video_filter: str, frequency: int) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", video_filter,
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def _make_audio(path: Path, duration: float = 1.0) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=523:duration={duration}", "-c:a", "aac", str(path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
