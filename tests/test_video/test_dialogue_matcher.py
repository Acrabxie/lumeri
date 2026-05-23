import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.dialogue_matcher import render_dialogue_matcher_plan


def test_dialogue_matcher_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.dialogue_matcher.render_dialogue_matcher_plan" in catalog_for_prompt("video")


def test_dialogue_matcher_writes_match_metadata(tmp_path: Path) -> None:
    tone_video_path = tmp_path / "tone_video.mp4"
    _make_tone_video(tone_video_path)

    output = tmp_path / "dialogue-matched.mp4"
    result = render_dialogue_matcher_plan(str(tone_video_path), str(tone_video_path), str(output), sample_seconds=1.0)
    metadata = json.loads(output.with_suffix(".dialogue_matcher.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_dialogue_matcher"
    assert metadata["analysis_results"]["reference"]["has_audio"] is True
    assert metadata["analysis_results"]["target"]["has_audio"] is True
    assert metadata["level_delta_db"] == pytest.approx(0.0, abs=0.25)
    assert metadata["match_actions"]


def test_dialogue_matcher_missing_audio_diagnostic(tmp_path: Path) -> None:
    silent = tmp_path / "silent.mp4"
    _make_silent_video(silent)
    output = tmp_path / "silent-matched.mp4"
    render_dialogue_matcher_plan(str(silent), str(silent), str(output), sample_seconds=0.5)
    metadata = json.loads(output.with_suffix(".dialogue_matcher.json").read_text(encoding="utf-8"))
    assert output.exists()
    assert metadata["analysis_results"]["reference"]["has_audio"] is False
    assert metadata["analysis_results"]["target"]["has_audio"] is False
    assert metadata["level_delta_db"] is None
    assert any("no audio stream" in item for item in metadata["diagnostics"])


def test_dialogue_matcher_reproduces_two_real_examples(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        return
    for index, target in enumerate(real_inputs, 1):
        output = tmp_path / f"real-{index}-dialogue-matcher.mp4"
        render_dialogue_matcher_plan(str(real_inputs[0]), str(target), str(output), sample_seconds=1.0)
        metadata = json.loads(output.with_suffix(".dialogue_matcher.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert output.stat().st_size > 0
        assert metadata["effect"] == "resolve21_ai_dialogue_matcher"
        assert "reference" in metadata["analysis_results"]
        assert "target" in metadata["analysis_results"]


def _make_silent_video(path: Path) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=0.8",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def _make_tone_video(path: Path) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=0.8",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.8",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
