import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.music_editor import render_ai_music_editor_plan


def test_music_editor_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.music_editor.render_ai_music_editor_plan" in catalog_for_prompt("video")


def test_music_editor_writes_metadata_and_preview(tmp_path: Path) -> None:
    video_path = tmp_path / "dummy_video.mp4"
    _make_dummy_video(video_path, duration=3.0)
    music_path = tmp_path / "dummy_music.m4a"
    _make_dummy_audio(music_path, duration=5.0)  # Music longer than video, should be trimmed

    output = tmp_path / "music_edited.mp4"
    result = render_ai_music_editor_plan(str(video_path), str(music_path), str(output), fade_seconds=0.1)

    metadata = json.loads(output.with_suffix(".music_editor.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert output.stat().st_size > 0
    assert metadata["effect"] == "resolve21_ai_music_editor"
    assert metadata["video_path"] == str(video_path)
    assert metadata["music_path"] == str(music_path)
    assert metadata["output_path"] == str(output)
    assert pytest.approx(metadata["video_duration_seconds"], abs=0.1) == 3.0
    assert pytest.approx(metadata["target_duration_seconds"], abs=0.1) == 3.0
    assert "trimming" in metadata["diagnostics"][0]
    assert any("Confirm music fits" in hint for hint in metadata["review_hints"])
    assert "section_markers" in metadata
    assert isinstance(metadata["section_markers"], list)
    assert len(metadata["section_markers"]) == metadata["section_count"]
    assert "edit_decisions" in metadata
    assert isinstance(metadata["edit_decisions"], dict)
    assert "type" in metadata["edit_decisions"]


def test_music_editor_loops_short_music(tmp_path: Path) -> None:
    video_path = tmp_path / "dummy_video_long.mp4"
    _make_dummy_video(video_path, duration=5.0)
    music_path = tmp_path / "dummy_music_short.m4a"
    _make_dummy_audio(music_path, duration=2.0) # Music shorter than video, should be looped

    output = tmp_path / "music_looped.mp4"
    render_ai_music_editor_plan(str(video_path), str(music_path), str(output), fade_seconds=0.1)

    metadata = json.loads(output.with_suffix(".music_editor.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert output.stat().st_size > 0
    assert pytest.approx(metadata["video_duration_seconds"], abs=0.1) == 5.0
    assert pytest.approx(metadata["target_duration_seconds"], abs=0.1) == 5.0
    assert "looping" in metadata["diagnostics"][0]
    assert "section_markers" in metadata
    assert isinstance(metadata["section_markers"], list)
    assert len(metadata["section_markers"]) == metadata["section_count"]
    assert "edit_decisions" in metadata
    assert isinstance(metadata["edit_decisions"], dict)
    assert "type" in metadata["edit_decisions"]


def test_music_editor_clamps_target_and_fade_before_render(tmp_path: Path) -> None:
    video_path = tmp_path / "dummy_video_short.mp4"
    _make_dummy_video(video_path, duration=1.0) # 1 second video
    music_path = tmp_path / "dummy_music_long.m4a"
    _make_dummy_audio(music_path, duration=4.0) # 4 second audio

    output = tmp_path / "music_clamped.mp4"

    # target_duration_seconds > video_duration, fade_seconds > half video_duration
    render_ai_music_editor_plan(
        str(video_path), str(music_path), str(output),
        target_duration_seconds=3.0, # Will be clamped to 1.0
        fade_seconds=2.0, # Will be clamped to 0.5 (1.0 / 2)
    )

    metadata = json.loads(output.with_suffix(".music_editor.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert output.stat().st_size > 0

    assert pytest.approx(metadata["video_duration_seconds"], abs=0.1) == 1.0
    assert pytest.approx(metadata["music_duration_seconds"], abs=0.1) == 4.0
    assert pytest.approx(metadata["target_duration_seconds"], abs=0.1) == 1.0 # Clamped to video duration
    assert pytest.approx(metadata["fade_seconds"], abs=0.1) == 0.5 # Clamped to half target duration (1.0 / 2)

    assert "Clamping to video duration" in metadata["diagnostics"][0]
    assert any("clamped" in item for item in metadata["diagnostics"])
    # Test section_count=0 raises ValueError
    with pytest.raises(ValueError, match="section_count must be greater than 0."):
        render_ai_music_editor_plan(
            str(video_path), str(music_path), str(tmp_path / "fail.mp4"),
            section_count=0
        )


def test_music_editor_with_real_footage(tmp_path: Path) -> None:
    real_video_paths = [
        Path("inputs/demo.mp4"),
        Path("inputs/gemia_timeline_demo.mp4"),
    ]
    # Filter for existing files
    existing_real_videos = [p for p in real_video_paths if p.exists()]

    if not existing_real_videos:
        pytest.skip("No real input videos found for testing (inputs/demo.mp4 or inputs/gemia_timeline_demo.mp4)")

    music_path = tmp_path / "generated_music.m4a"
    _make_dummy_audio(music_path, duration=10.0) # Use a reasonably long dummy audio

    for index, video_p in enumerate(existing_real_videos):
        output_p = tmp_path / f"real_footage_music_edited_{index}.mp4"
        result = render_ai_music_editor_plan(str(video_p), str(music_path), str(output_p), fade_seconds=0.2)

        metadata = json.loads(output_p.with_suffix(".music_editor.json").read_text(encoding="utf-8"))
        assert result == str(output_p)
        assert output_p.exists()
        assert output_p.stat().st_size > 0
        assert metadata["effect"] == "resolve21_ai_music_editor"
        assert metadata["video_path"] == str(video_p.resolve())
        assert metadata["music_path"] == str(music_path)
        assert any("Confirm music fits" in hint for hint in metadata["review_hints"])


def _make_dummy_video(path: Path, duration: float = 1.0) -> None:
    """Helper to create a dummy video file for testing."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=1280x720:r=30:d={duration}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"ffmpeg error: {proc.stderr}"


def _make_dummy_audio(path: Path, duration: float = 1.0, freq: int = 440) -> None:
    """Helper to create a dummy audio file for testing."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
            "-c:a", "aac", "-b:a", "128k",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"ffmpeg error: {proc.stderr}"
