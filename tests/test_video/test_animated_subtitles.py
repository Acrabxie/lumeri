import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.animated_subtitles import render_ai_animated_subtitles_plan


def test_animated_subtitles_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.animated_subtitles.render_ai_animated_subtitles_plan" in catalog_for_prompt("video")


def test_animated_subtitles_writes_layer_plan_and_preview(tmp_path: Path) -> None:
    video_path = tmp_path / "source.mp4"
    _make_dummy_video(video_path, duration=2.0)
    output = tmp_path / "subtitled.mp4"
    result = render_ai_animated_subtitles_plan(
        str(video_path),
        str(output),
        word_timings=[
            {"word": "Cut", "start_seconds": 0.1, "end_seconds": 0.55},
            {"word": "on", "start_seconds": 0.55, "end_seconds": 0.9},
            {"word": "motion", "start_seconds": 0.9, "end_seconds": 1.45},
        ],
        font_size=36,
    )

    metadata = json.loads(output.with_suffix(".animated_subtitles.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert output.stat().st_size > 0
    assert metadata["effect"] == "resolve21_ai_animated_subtitles"
    assert metadata["render_mode"] in {"ffmpeg_drawtext_word_layers", "pil_word_layer_fallback"}
    assert metadata["word_count"] == 3
    assert len(metadata["subtitle_layers"]) == 3
    assert metadata["subtitle_layers"][0]["keyframes"][1]["scale"] > 1.0
    assert any("active words animate" in hint for hint in metadata["review_hints"])


def test_animated_subtitles_derives_timing_from_transcript(tmp_path: Path) -> None:
    video_path = tmp_path / "source.mp4"
    _make_dummy_video(video_path, duration=1.5)
    output = tmp_path / "transcript.mp4"
    render_ai_animated_subtitles_plan(
        str(video_path),
        str(output),
        transcript="hello precise subtitles",
        target_duration_seconds=1.2,
        font_size=34,
    )

    metadata = json.loads(output.with_suffix(".animated_subtitles.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert metadata["word_count"] == 3
    assert pytest.approx(metadata["target_duration_seconds"], abs=0.01) == 1.2
    assert metadata["word_timings"][0]["word"] == "hello"
    assert metadata["word_timings"][-1]["end_seconds"] <= 1.2


def test_animated_subtitles_rejects_empty_words(tmp_path: Path) -> None:
    video_path = tmp_path / "source.mp4"
    _make_dummy_video(video_path, duration=1.0)
    with pytest.raises(ValueError, match="at least one word"):
        render_ai_animated_subtitles_plan(str(video_path), str(tmp_path / "out.mp4"), transcript="  ")


def test_animated_subtitles_with_real_footage(tmp_path: Path) -> None:
    real_video_paths = [
        Path("inputs/demo.mp4"),
        Path("inputs/gemia_timeline_demo.mp4"),
    ]
    existing = [path for path in real_video_paths if path.exists()]
    if not existing:
        pytest.skip("No real input videos found for testing")

    for index, video_path in enumerate(existing[:2]):
        output = tmp_path / f"real_animated_subtitles_{index}.mp4"
        render_ai_animated_subtitles_plan(
            str(video_path),
            str(output),
            word_timings=[
                {"word": "Review", "start_seconds": 0.0, "end_seconds": 0.45},
                {"word": "the", "start_seconds": 0.45, "end_seconds": 0.75},
                {"word": "cut", "start_seconds": 0.75, "end_seconds": 1.2},
            ],
            font_size=42,
            target_duration_seconds=1.4,
        )
        metadata = json.loads(output.with_suffix(".animated_subtitles.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert output.stat().st_size > 0
        assert metadata["source_path"] == str(video_path.resolve())
        assert metadata["word_count"] == 3


def _make_dummy_video(path: Path, duration: float = 1.0) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=640x360:r=24:d={duration}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr