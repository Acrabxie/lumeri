import json
import sys
import types
import subprocess
from pathlib import Path

import pytest

from gemia.audio.speaker_timeline import normalize_speaker_segments, render_pyannote_speaker_timeline_backend
from gemia.registry import catalog_for_prompt, clear_catalog_cache


def test_speaker_timeline_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.audio.speaker_timeline.render_pyannote_speaker_timeline_backend" in catalog_for_prompt("audio")


def test_speaker_timeline_fallback_writes_segments(tmp_path: Path) -> None:
    media = tmp_path / "dialogue.mp4"
    _make_video(media, duration=2.0, frequency=440)
    metadata_path = Path(render_pyannote_speaker_timeline_backend(str(media), use_pyannote="fallback"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["effect"] == "github_pyannote_audio_speaker_timeline_backend"
    assert metadata["backend"] == "ffmpeg_silence_fallback"
    assert metadata["speaker_count"] >= 1
    assert metadata["speaker_segments"][0]["start_seconds"] == 0.0


def test_speaker_timeline_pyannote_adapter_normalizes_output(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "dialogue.wav"
    _make_audio(media, duration=1.5)

    class Turn:
        def __init__(self, start: float, end: float) -> None:
            self.start = start
            self.end = end

    class Pipeline:
        @classmethod
        def from_pretrained(cls, model: str, **kwargs):
            assert model == "local-test-model"
            assert kwargs["token"] == "token"
            return cls()

        def __call__(self, path: str, **kwargs):
            assert Path(path) == media.resolve()
            assert kwargs["num_speakers"] == 2
            return types.SimpleNamespace(speaker_diarization=[(Turn(0.0, 0.6), "SPEAKER_A"), (Turn(0.6, 1.4), "SPEAKER_B")])

    pkg = types.ModuleType("pyannote")
    audio = types.ModuleType("pyannote.audio")
    audio.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "pyannote", pkg)
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)

    metadata_path = Path(render_pyannote_speaker_timeline_backend(
        str(media), pipeline_model="local-test-model", auth_token="token", use_pyannote="pyannote", num_speakers=2
    ))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["backend"] == "pyannote.audio"
    assert [item["speaker"] for item in metadata["speaker_segments"]] == ["SPEAKER_A", "SPEAKER_B"]


def test_normalize_speaker_segments_merges_adjacent() -> None:
    segments = normalize_speaker_segments([
        {"speaker": "Host", "start_seconds": 0.0, "end_seconds": 0.5},
        {"speaker": "Host", "start_seconds": 0.51, "end_seconds": 1.0},
        {"speaker": "Guest", "start_seconds": 1.1, "end_seconds": 1.6},
        {"speaker": "Guest", "start_seconds": 1.7, "end_seconds": 1.8},
    ])
    assert segments == [
        {"speaker": "Host", "start_seconds": 0.0, "end_seconds": 1.0, "duration_seconds": 1.0},
        {"speaker": "Guest", "start_seconds": 1.1, "end_seconds": 1.6, "duration_seconds": 0.5},
    ]


def test_speaker_timeline_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for speaker timeline testing")
    for index, media in enumerate(real_inputs, 1):
        metadata_path = tmp_path / f"speaker_timeline_{index}.json"
        render_pyannote_speaker_timeline_backend(str(media), str(metadata_path), use_pyannote="fallback")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["speaker_segments"]
        assert metadata["input_path"] == str(media.resolve())


def _make_video(path: Path, duration: float, frequency: int) -> None:
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=160x90:r=12:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _make_audio(path: Path, duration: float) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
