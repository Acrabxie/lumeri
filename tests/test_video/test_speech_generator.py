import pytest
import json
import subprocess
from pathlib import Path

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.speech_generator import render_ai_speech_generator_plan, _probe_duration


def test_speech_generator_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.speech_generator.render_ai_speech_generator_plan" in catalog_for_prompt("video")


def test_render_ai_speech_generator_plan_rejects_real_generation(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "speech-attached.mp4"
    with pytest.raises(ValueError, match="dry_run=False is not supported"):
        render_ai_speech_generator_plan(
            sample_video_path,
            str(output),
            script="This should not be generated for real.",
            dry_run=False,
        )


def test_render_ai_speech_generator_plan_writes_voiceover_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "speech-attached.mp4"

    result = render_ai_speech_generator_plan(
        sample_video_path,
        str(output),
        script="Welcome to the timeline. This is a generated narration placeholder.",
        voice="calm_editor",
        performance="measured tutorial",
        target_duration_seconds=2.0,
    )

    metadata_path = output.with_suffix(".speech_generator.json")
    audio_path = output.with_suffix(".speech_generator.wav")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert audio_path.exists()
    assert metadata["effect"] == "resolve21_ai_speech_generator"
    assert metadata["generation"]["dry_run"] is True
    assert metadata["generation"]["model_request_ready"] is True
    assert metadata["voice"]["name"] == "calm_editor"
    assert metadata["timing"]["word_count"] == 10
    assert metadata["timeline_attachment"]["track_id"] == "A2"
    assert _has_audio_stream(output)


def test_speech_generator_reproduces_two_real_timelines(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        return

    for index, source in enumerate(real_inputs, 1):
        output = tmp_path / f"real-{index}-speech-generator.mp4"
        render_ai_speech_generator_plan(
            str(source),
            str(output),
            script=f"Timeline {index} has a repeatable dry run narration for review.",
            voice="review_narrator",
            performance="confident edit review",
            target_duration_seconds=1.8,
        )
        metadata = json.loads(output.with_suffix(".speech_generator.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert output.stat().st_size > 0
        assert metadata["timing"]["word_count"] >= 8
        assert _has_audio_stream(output)



def _has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def test_render_ai_speech_generator_plan_with_start_seconds_delays_audio(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "delayed-speech.mp4"
    delay = 1.5
    script = "This is a delayed narration."
    result = render_ai_speech_generator_plan(
        sample_video_path,
        str(output),
        script=script,
        start_seconds=delay,
        target_duration_seconds=2.0,
    )

    metadata_path = output.with_suffix(".speech_generator.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["timeline_attachment"]["start_seconds"] == delay
    # speech_duration is ~0.9s for "This is a delayed narration." based on dry run logic
    assert metadata["timeline_attachment"]["end_seconds"] == round(delay + metadata["timing"]["speech_duration_seconds"], 3)

    voice_audio_path = output.with_suffix(".speech_generator.wav")
    voice_audio_duration = _probe_duration(voice_audio_path)
    output_video_duration = _probe_duration(output)

    # The output video's total duration should be approximately the delay plus the voiceover duration.
    # We allow a small tolerance due to ffmpeg's encoding and stream handling.
    # The output video should be at least as long as the expected end time of the speech in the metadata,
    # with a small tolerance.
    expected_min_duration = metadata["timeline_attachment"]["end_seconds"]
    assert output_video_duration == pytest.approx(expected_min_duration, abs=0.2)
    assert _has_audio_stream(output)
